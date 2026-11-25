"""Voces: convierten la traducción en audio.

- Windows: las voces que ya trae el sistema. Gratis, sin descargar nada.
- Piper: voces neuronales gratis que corren en la PC (más naturales). Se descarga aparte la primera vez;
  corre como programa separado (usa espeak-ng, que es GPL, así que no se mezcla con el código de Revoz).
- ElevenLabs: voces por API, con la clave de la persona.

Cada voz se identifica como "Proveedor:id", por ejemplo "Windows:Microsoft Raul".
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import wave
import zipfile
from dataclasses import dataclass
from pathlib import Path

from revoz.version import USER_AGENT
from revoz.core.providers import ProviderError, _check_url, get_json

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass
class Voice:
    provider: str
    id: str
    name: str
    language: str  # ej. "es-MX"
    gender: str = ""
    download_mb: int = 0  # > 0 si hay que descargarla antes de usarla

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.id}"


@dataclass
class Job:
    text: str
    voice_id: str
    out: Path
    speed: float = 1.0  # 1 = normal; > 1 más rápido


class TTSProvider:
    name = ""
    needs_key = False

    def voices(self, key: str = "") -> list[Voice]:
        raise NotImplementedError

    def synthesize(self, jobs: list[Job], key: str = "") -> None:
        """Genera un WAV por trabajo. Si uno falla, lanza ProviderError."""
        raise NotImplementedError


def split_key(voice_key: str) -> tuple[str, str]:
    provider, _, voice_id = voice_key.partition(":")
    return provider, voice_id


def matches_language(voice_lang: str, target: str) -> bool:
    """es-MX sirve para es-419 y es; es-ES sirve para es."""
    v, t = voice_lang.lower().replace("_", "-"), target.lower()
    if v.split("-")[0] != t.split("-")[0]:
        return False
    return True


# --- Windows ---

# Voces "OneCore" de Windows 10/11 (las que trae el sistema, incluidas las de hombre en español).
# Se usan desde el PowerShell que viene con Windows, sin instalar nada.
_PS_HEADER = r"""
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
  $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $type) { $t = $asTaskGeneric.MakeGenericMethod($type).Invoke($null, @($op)); $t.Wait(-1) | Out-Null; $t.Result }
[Windows.Media.SpeechSynthesis.SpeechSynthesizer, Windows.Media.SpeechSynthesis, ContentType = WindowsRuntime] | Out-Null
[Windows.Storage.Streams.DataReader, Windows.Storage.Streams, ContentType = WindowsRuntime] | Out-Null
$all = [Windows.Media.SpeechSynthesis.SpeechSynthesizer]::AllVoices
"""

_PS_LIST = _PS_HEADER + r"""
$all | ForEach-Object { [pscustomobject]@{ name = $_.DisplayName; lang = $_.Language; gender = "$($_.Gender)" } } | ConvertTo-Json -Compress
"""

# Lee los textos de un archivo JSON (nunca se arman comandos con el texto: no hay forma de inyectar nada).
_PS_SPEAK = "param([string]$jobsFile)\n" + _PS_HEADER + r"""
$jobs = Get-Content -Raw -Encoding UTF8 $jobsFile | ConvertFrom-Json
$synth = New-Object Windows.Media.SpeechSynthesis.SpeechSynthesizer
foreach ($j in $jobs) {
  $voice = ($all | Where-Object { $_.DisplayName -eq $j.voice })[0]
  if (-not $voice) { throw "No existe la voz $($j.voice)" }  # sin esto Windows usaría su voz por defecto
  $synth.Voice = $voice
  $synth.Options.SpeakingRate = [double]$j.speed
  $stream = Await ($synth.SynthesizeTextToStreamAsync($j.text)) ([Windows.Media.SpeechSynthesis.SpeechSynthesisStream])
  $reader = New-Object Windows.Storage.Streams.DataReader($stream.GetInputStreamAt(0))
  $size = [uint32]$stream.Size
  Await ($reader.LoadAsync($size)) ([uint32]) | Out-Null
  $bytes = New-Object byte[] $size
  $reader.ReadBytes($bytes)
  [IO.File]::WriteAllBytes($j.out, $bytes)
}
"""


def _powershell(script: str, *args: str, timeout: float = 600) -> str:
    with tempfile.TemporaryDirectory(prefix="revoz_tts_") as tmp:
        ps1 = Path(tmp) / "voz.ps1"
        ps1.write_text(script, "utf-8-sig")
        cmd = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(ps1), *args]
        r = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, creationflags=NO_WINDOW
        )
    if r.returncode != 0:
        first = next((ln.strip() for ln in r.stderr.splitlines() if ln.strip()), "?")  # la primera línea es el mensaje
        raise ProviderError("Las voces de Windows dieron un error: " + first[:200])
    return r.stdout


class WindowsVoices(TTSProvider):
    name = "Windows"

    def voices(self, key: str = "") -> list[Voice]:
        if sys.platform != "win32":
            return []
        out = _powershell(_PS_LIST, timeout=60).strip()
        data = json.loads(out) if out else []
        data = data if isinstance(data, list) else [data]
        return [Voice(self.name, d["name"], d["name"], d["lang"], d.get("gender", "")) for d in data]

    def synthesize(self, jobs: list[Job], key: str = "") -> None:
        if not jobs:
            return
        rows = []
        for j in jobs:
            j.out.parent.mkdir(parents=True, exist_ok=True)
            rows.append({"text": j.text, "voice": j.voice_id, "out": str(j.out), "speed": max(0.5, min(3.0, j.speed))})
        with tempfile.TemporaryDirectory(prefix="revoz_tts_") as tmp:
            jobs_file = Path(tmp) / "jobs.json"
            jobs_file.write_text(json.dumps(rows, ensure_ascii=False), "utf-8")
            _powershell(_PS_SPEAK, str(jobs_file), timeout=60 + 30 * len(jobs))
        missing = [j.out for j in jobs if not j.out.is_file()]
        if missing:
            raise ProviderError(f"Windows no generó el audio de {len(missing)} línea(s)")


# --- Piper ---

PIPER_URL = "https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_windows_amd64.zip"
PIPER_VOICES = "https://huggingface.co/rhasspy/piper-voices/resolve/main/"


def _download(url: str, dest: Path, progress=None) -> None:
    _check_url(url)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
            total, done = int(r.headers.get("Content-Length") or 0), 0
            while chunk := r.read(1 << 16):
                f.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    except OSError as e:
        tmp.unlink(missing_ok=True)
        raise ProviderError("No se pudo descargar: " + str(e)) from None
    os.replace(tmp, dest)


class Piper(TTSProvider):
    name = "Piper"

    def __init__(self, folder: Path) -> None:
        self.folder = folder  # ej. %APPDATA%/Revoz/piper

    @property
    def exe(self) -> Path:
        return self.folder / "piper" / "piper.exe"

    def installed(self) -> bool:
        return self.exe.is_file()

    def install(self, progress=None) -> None:
        """Descarga Piper (~21 MB) desde su página oficial en GitHub y lo descomprime con cuidado."""
        zip_path = self.folder / "piper.zip"
        _download(PIPER_URL, zip_path, progress)
        base = self.folder.resolve()
        with zipfile.ZipFile(zip_path) as zf:
            for m in zf.infolist():  # que ningún archivo del zip salga de la carpeta
                if not (self.folder / m.filename).resolve().is_relative_to(base):
                    raise ProviderError("El archivo de Piper descargado no es válido")
            zf.extractall(self.folder)
        zip_path.unlink(missing_ok=True)

    def _catalog(self) -> dict:
        cache = self.folder / "voices.json"
        if not cache.is_file():
            _download(PIPER_VOICES + "voices.json", cache)
        return json.loads(cache.read_text("utf-8"))

    def voices(self, key: str = "") -> list[Voice]:
        out = []
        for vid, info in self._catalog().items():
            files = info.get("files", {})
            onnx = next((f for f in files if f.endswith(".onnx")), "")
            mb = 0 if (self.folder / "voices" / f"{vid}.onnx").is_file() else round(files.get(onnx, {}).get("size_bytes", 0) / 1e6)
            lang = info.get("language", {}).get("code", "").replace("_", "-")
            out.append(Voice(self.name, vid, f"{info.get('name', vid)} ({info.get('quality', '')})", lang, "", mb))
        return out

    def _ensure_voice(self, vid: str, progress=None) -> Path:
        model = self.folder / "voices" / f"{vid}.onnx"
        if model.is_file():
            return model
        files = self._catalog().get(vid, {}).get("files", {})
        for rel in files:
            if rel.endswith((".onnx", ".onnx.json")):
                _download(PIPER_VOICES + rel, self.folder / "voices" / Path(rel).name, progress)
        if not model.is_file():
            raise ProviderError(f"No se encontró la voz de Piper {vid}")
        return model

    def synthesize(self, jobs: list[Job], key: str = "") -> None:
        if not self.installed():
            raise ProviderError("Piper no está instalado")
        for vid in {j.voice_id for j in jobs}:
            group = [j for j in jobs if j.voice_id == vid]
            model = self._ensure_voice(vid)
            lines = []
            for j in group:
                j.out.parent.mkdir(parents=True, exist_ok=True)
                lines.append(json.dumps({"text": j.text, "output_file": str(j.out), "length_scale": round(1 / j.speed, 3)}, ensure_ascii=False))
            r = subprocess.run(
                [str(self.exe), "--model", str(model), "--json-input"],
                input="\n".join(lines) + "\n", capture_output=True, text=True, encoding="utf-8",
                timeout=60 + 30 * len(group), creationflags=NO_WINDOW,
            )  # fmt: skip
            if r.returncode != 0 or any(not j.out.is_file() for j in group):
                raise ProviderError("Piper no pudo generar el audio")


# --- ElevenLabs ---


class ElevenLabsVoices(TTSProvider):
    name = "ElevenLabs"
    needs_key = True
    MODEL = "eleven_multilingual_v2"
    RATE = 24000

    def voices(self, key: str = "") -> list[Voice]:
        data = get_json("https://api.elevenlabs.io/v1/voices", {"xi-api-key": key})
        out = []
        for v in data.get("voices", []):
            labels = v.get("labels") or {}
            out.append(Voice(self.name, v["voice_id"], v.get("name", v["voice_id"]), "multi", labels.get("gender", "")))
        return out

    def synthesize(self, jobs: list[Job], key: str = "") -> None:
        for j in jobs:
            url = f"https://api.elevenlabs.io/v1/text-to-speech/{urllib.request.quote(j.voice_id)}?output_format=pcm_{self.RATE}"
            body = {"text": j.text, "model_id": self.MODEL, "voice_settings": {"stability": 0.5, "similarity_boost": 0.75, "speed": j.speed}}
            pcm = post_bytes(url, {"xi-api-key": key}, body)
            j.out.parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(j.out), "wb") as w:  # ElevenLabs manda PCM crudo: se le pone cabecera WAV
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(self.RATE)
                w.writeframes(pcm)


def post_bytes(url: str, headers: dict[str, str], body: object, timeout: float = 180) -> bytes:
    _check_url(url)
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"User-Agent": USER_AGENT, "Content-Type": "application/json", **headers})  # fmt: skip
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(200_000_000)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise ProviderError("Clave inválida o sin permisos") from None
        raise ProviderError(f"El servicio respondió con error {e.code}", retry=e.code == 429 or e.code >= 500) from None
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        raise ProviderError("No se pudo conectar con el servicio", retry=True) from None


def copy_wav(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
