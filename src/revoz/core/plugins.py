"""Sistema de plugins.

Un plugin es una carpeta dentro de la carpeta de plugins con:
  plugin.toml  - metadatos (name, version, author, kind, appids)
  plugin.py    - define una clase `Plugin` que hereda de GamePlugin o Provider

Un plugin ejecuta código en la PC del usuario, así que solo se carga si el usuario
lo marcó como confiable. Si cambia cualquier archivo del plugin, hay que volver a confirmarlo.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import shutil
import tempfile
import tomllib
import zipfile
from dataclasses import dataclass
from pathlib import Path

from revoz.core.providers import Provider

GAME, PROVIDER = "game", "provider"
# appid de Steam, o id de Epic/GOG con prefijo
_APPID = re.compile(r"\d+|(epic|gog):[\w.-]+")


class PluginError(Exception):
    pass


class GamePlugin:
    """Base para plugins de juego. Implementá lo que soporte tu plugin."""

    def list_audio(self, game_path: Path) -> list[str]:
        """Ids de los audios del juego (por ejemplo, rutas internas)."""
        raise NotImplementedError

    def extract(self, game_path: Path, audio_id: str, out_dir: Path) -> Path:
        """Extrae un audio a out_dir como WAV y devuelve la ruta."""
        raise NotImplementedError

    def replace(self, game_path: Path, files: dict[str, Path]) -> None:
        """Reemplaza audios del juego {audio_id: wav_nuevo}. Debe hacer backup antes."""
        raise NotImplementedError


@dataclass(frozen=True)
class PluginInfo:
    id: str  # nombre de la carpeta
    name: str
    version: str
    author: str
    kind: str
    appids: tuple[str, ...]
    path: Path
    digest: str


def digest(folder: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(p for p in folder.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
        h.update(f.relative_to(folder).as_posix().encode() + b"\0")
        h.update(f.read_bytes())
    return h.hexdigest()


def _read_info(folder: Path) -> PluginInfo:
    try:
        meta = tomllib.loads((folder / "plugin.toml").read_text("utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise PluginError(f"plugin.toml inválido: {e}") from None
    if not (folder / "plugin.py").is_file():
        raise PluginError("falta plugin.py")
    name, kind, appids = meta.get("name"), meta.get("kind"), meta.get("appids", [])
    if not isinstance(name, str) or not name.strip():
        raise PluginError("falta 'name'")
    if kind not in (GAME, PROVIDER):
        raise PluginError("'kind' debe ser \"game\" o \"provider\"")
    if not isinstance(appids, list) or not all(isinstance(a, str) and _APPID.fullmatch(a) for a in appids):
        raise PluginError("'appids' debe ser una lista como [\"12210\", \"epic:Nombre\", \"gog:1234\"]")
    if kind == GAME and not appids:
        raise PluginError("un plugin de juego necesita al menos un appid")
    return PluginInfo(
        folder.name,
        name.strip(),
        str(meta.get("version", "?")),
        str(meta.get("author", "desconocido")),
        kind,
        tuple(appids),
        folder,
        digest(folder),
    )


def discover(root: Path) -> tuple[list[PluginInfo], list[str]]:
    """Lee los plugins de root sin ejecutar nada. Devuelve (plugins, errores)."""
    found, errors = [], []
    if not root.is_dir():
        return found, errors
    for folder in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        try:
            found.append(_read_info(folder))
        except PluginError as e:
            errors.append(f"{folder.name}: {e}")
    return found, errors


MAX_ZIP_BYTES = 200 * 1024 * 1024
MAX_ZIP_FILES = 5000


def _extract_zip(source: Path, dest: Path) -> None:
    try:
        with zipfile.ZipFile(source) as zf:
            members = zf.infolist()
            if len(members) > MAX_ZIP_FILES or sum(m.file_size for m in members) > MAX_ZIP_BYTES:
                raise PluginError("el .zip es demasiado grande")
            base = dest.resolve()
            for m in members:  # evita "zip slip": archivos que intentan salir de la carpeta
                if not (dest / m.filename).resolve().is_relative_to(base):
                    raise PluginError(f"el .zip contiene una ruta peligrosa: {m.filename}")
            zf.extractall(dest)
    except zipfile.BadZipFile:
        raise PluginError("el archivo no es un .zip válido") from None


def _plugin_root(folder: Path) -> Path:
    if (folder / "plugin.toml").is_file():
        return folder
    subdirs = [d for d in folder.iterdir() if d.is_dir()]
    if len(subdirs) == 1 and (subdirs[0] / "plugin.toml").is_file():
        return subdirs[0]
    raise PluginError("no se encontró plugin.toml")


def install(source: Path, root: Path) -> PluginInfo:
    """Copia un plugin (.zip o carpeta) dentro de root. No ejecuta nada:
    el plugin igual tiene que ser aprobado antes de cargarse."""
    root.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(dir=root, prefix=".instalando_"))
    try:
        if source.is_dir():
            shutil.copytree(source, tmp, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            _extract_zip(source, tmp)
        folder = _plugin_root(tmp)
        _read_info(folder)  # valida antes de instalar
        name = folder.name if folder != tmp else source.stem
        dest = root / (re.sub(r"[^\w.-]", "_", name).strip("._") or "plugin")
        if dest.exists():
            raise PluginError(f"ya hay un plugin instalado en la carpeta '{dest.name}'")
        folder.rename(dest)
        return _read_info(dest)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def load_trusted(file: Path) -> dict[str, str]:
    try:
        return json.loads(file.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def is_trusted(trusted: dict[str, str], info: PluginInfo) -> bool:
    return trusted.get(info.id) == info.digest


def trust(file: Path, trusted: dict[str, str], info: PluginInfo) -> None:
    trusted[info.id] = info.digest
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(trusted, indent=1), "utf-8")


def load(info: PluginInfo, trusted: dict[str, str]) -> GamePlugin | Provider:
    # Se recalcula el hash justo antes de ejecutar: si alguien tocó el plugin, no se carga.
    if trusted.get(info.id) != digest(info.path):
        raise PluginError("el plugin no es confiable o cambió desde que se aprobó")
    spec = importlib.util.spec_from_file_location(f"revoz_plugin_{info.id}", info.path / "plugin.py")
    if spec is None or spec.loader is None:
        raise PluginError("no se pudo cargar plugin.py")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        cls = getattr(module, "Plugin")
    except Exception as e:
        raise PluginError(f"error al cargar: {type(e).__name__}: {e}") from None
    base = GamePlugin if info.kind == GAME else Provider
    if not (isinstance(cls, type) and issubclass(cls, base)):
        raise PluginError(f"la clase Plugin debe heredar de {base.__name__}")
    try:
        return cls()
    except Exception as e:
        raise PluginError(f"error al iniciar: {type(e).__name__}: {e}") from None
