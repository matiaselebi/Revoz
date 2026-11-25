import json
from pathlib import Path

import pytest

from revoz.core import project as pj
from revoz.core.cache import Cache
from revoz.core.engine import Engine, Options
from revoz.core.providers import Provider, ProviderError
from revoz.core.transcribe import Transcript


class FakeWhisper:
    """Transcribe leyendo el texto escrito dentro del 'audio' de prueba."""

    loads = 0
    calls = 0

    def __init__(self, model, models_dir):
        FakeWhisper.loads += 1

    def transcribe(self, path, start=None, end=None):
        FakeWhisper.calls += 1
        text = Path(path).read_text("utf-8")
        if start is not None or end is not None:  # el "audio" de prueba es texto: el tramo son caracteres
            text = text[int(start or 0) : None if end is None else int(end)]
        if text == "BANG":
            return Transcript("", "en", True, False)
        return Transcript(text, "en", False, text.endswith("?"))


class FakeAI(Provider):
    name = "Fake"
    can_translate = True

    def __init__(self, fail_times=0, error=None):
        self.requests = []
        self.fail_times = fail_times
        self.error = error

    def translate(self, key, request):
        if self.fail_times:
            self.fail_times -= 1
            raise self.error
        self.requests.append(request)
        return {i.id: f"[{request.target}] {i.text}" for i in request.items}


@pytest.fixture(autouse=True)
def _reset():
    FakeWhisper.loads = FakeWhisper.calls = 0


def _project(tmp_path: Path, texts: list[str]) -> pj.Project:
    lines = []
    for n, text in enumerate(texts):
        f = tmp_path / "game" / "sound" / ("barney" if n % 2 else "scientist") / f"{n}.wav"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, "utf-8")
        lines.append(pj.Line(f.relative_to(tmp_path / "game").as_posix(), str(f), 1.0))
    return pj.Project("1", "Juego", lines=lines)


def _engine(tmp_path, project, **kw):
    return Engine(project, tmp_path / "game", Cache(tmp_path / "cache.db"), tmp_path / "models",
                  transcriber_factory=FakeWhisper, sleep=lambda s: None, **kw)  # fmt: skip


def _opts(ai, **kw):
    return Options(provider=ai, key="k", model="m", target="es-419", usd_per_million_chars=10.0, **kw)


def test_full_pass_transcribes_translates_and_skips_noise(tmp_path: Path):
    project = _project(tmp_path, ["Hello Gordon", "BANG", "Catch me later", "Is that you?"])
    ai = FakeAI()
    s = _engine(tmp_path, project).run(_opts(ai))

    lines = project.lines
    assert [line.character for line in lines] == ["scientist", "barney", "scientist", "barney"]
    assert lines[0].translation == "[es-419] Hello Gordon" and lines[0].status == pj.TRANSLATED
    assert lines[1].no_voice and not lines[1].translation  # el disparo no se traduce ni se paga
    assert lines[3].doubtful
    assert (s.transcribed, s.translated, s.no_voice, s.doubtful, s.errors) == (3, 3, 1, 1, 0)
    assert s.spent == pytest.approx(len("Hello GordonCatch me laterIs that you?") / 1e6 * 10)
    assert project.spent == pytest.approx(s.spent)
    assert FakeWhisper.loads == 1  # el modelo se carga una sola vez
    assert ai.requests[0].items[0].character == "scientist"


def test_cache_means_never_paying_twice(tmp_path: Path):
    project = _project(tmp_path, ["Hello", "World"])
    _engine(tmp_path, project).run(_opts(FakeAI()))

    fresh = _project(tmp_path / "otra_copia", ["Hello", "World"])  # mismos audios en otra carpeta
    eng = Engine(fresh, tmp_path / "otra_copia" / "game", Cache(tmp_path / "cache.db"), tmp_path / "models",
                 transcriber_factory=FakeWhisper, sleep=lambda s: None)  # fmt: skip
    ai = FakeAI()
    FakeWhisper.calls = 0
    s = eng.run(_opts(ai))
    assert FakeWhisper.calls == 0 and ai.requests == []
    assert s.spent == 0 and s.from_cache == 4
    assert fresh.lines[1].translation == "[es-419] World"


def test_changing_glossary_translates_again(tmp_path: Path):
    project = _project(tmp_path, ["Hello Gordon"])
    _engine(tmp_path, project).run(_opts(FakeAI()))
    project.lines[0].translation = ""
    project.glossary = {"Gordon": ""}
    ai = FakeAI()
    _engine(tmp_path, project).run(_opts(ai))
    assert len(ai.requests) == 1 and ai.requests[0].glossary == {"Gordon": ""}


def test_test_mode_limits_lines(tmp_path: Path):
    project = _project(tmp_path, [f"line {n}" for n in range(10)])
    _engine(tmp_path, project).run(_opts(FakeAI(), max_lines=3))
    assert sum(bool(line.translation) for line in project.lines) == 3
    _engine(tmp_path, project).run(_opts(FakeAI()))  # la pasada completa sigue con el resto
    assert all(line.translation for line in project.lines)


def test_spending_limit_stops_before_going_over(tmp_path: Path):
    project = _project(tmp_path, ["x" * 2000, "y" * 2000, "z" * 2000])  # cada una en su lote
    s = _engine(tmp_path, project).run(_opts(FakeAI(), limit=0.045))  # alcanza para 2 líneas (0.02 c/u)
    assert s.stopped == "límite"
    assert sum(bool(line.translation) for line in project.lines) == 2
    assert project.spent <= 0.045


def test_retries_when_ai_cuts_then_continues(tmp_path: Path):
    project = _project(tmp_path, ["Hello"])
    ai = FakeAI(fail_times=2, error=ProviderError("límite de uso", retry=True))
    s = _engine(tmp_path, project).run(_opts(ai))
    assert project.lines[0].translation and s.stopped == ""


def test_invalid_key_stops_and_marks_error(tmp_path: Path):
    project = _project(tmp_path, ["Hello"])
    s = _engine(tmp_path, project).run(_opts(FakeAI(fail_times=1, error=ProviderError("Clave inválida"))))
    assert s.stopped == "Clave inválida" and project.lines[0].error == "Clave inválida"
    assert project.lines[0].original == "Hello"  # la transcripción no se pierde


def test_pause_stops_and_resume_continues(tmp_path: Path):
    project = _project(tmp_path, [f"line {n}" for n in range(6)])
    eng = _engine(tmp_path, project)
    eng.on_progress = lambda done, total, msg, i: eng.stop.set() if done == 2 else None
    s = eng.run(_opts(FakeAI()))
    assert s.stopped == "pausa"
    assert 0 < sum(bool(line.original) for line in project.lines) < 6
    _engine(tmp_path, project).run(_opts(FakeAI()))
    assert all(line.translation for line in project.lines)


def test_plugin_subtitles_avoid_transcribing(tmp_path: Path):
    project = _project(tmp_path, ["audio que no hace falta escuchar"])

    class Plugin:
        def subtitles(self, game_path):
            return {project.lines[0].id: "Texto exacto del juego"}

    _engine(tmp_path, project, plugin=Plugin()).run(_opts(FakeAI()))
    assert FakeWhisper.calls == 0
    assert project.lines[0].translation == "[es-419] Texto exacto del juego"


def test_broken_audio_does_not_stop_the_rest(tmp_path: Path):
    project = _project(tmp_path, ["Hello", "World"])
    Path(project.lines[0].path).unlink()
    project.lines[0].path = ""
    s = _engine(tmp_path, project).run(_opts(FakeAI()))
    assert project.lines[0].error and project.lines[1].translation == "[es-419] World"
    assert s.errors == 1


def test_project_with_new_fields_roundtrips(tmp_path: Path):
    p = pj.Project("1", "J", glossary={"Gordon": ""}, characters={"barney": "guardia simpático"}, spent=0.5, limit=2.0,
                   lines=[pj.Line("a", no_voice=True, doubtful=True, error="x")])  # fmt: skip
    pj.save(tmp_path / "p.json", p)
    assert pj.load(tmp_path / "p.json") == p
    # un proyecto viejo (sin los campos nuevos) se sigue abriendo
    (tmp_path / "viejo.json").write_text(json.dumps({"appid": "1", "game": "J", "lines": [{"id": "a"}]}))
    assert pj.load(tmp_path / "viejo.json").lines[0].no_voice is False


@pytest.mark.parametrize(
    "line_id,expected",
    [
        ("valve/sound/scientist/hola.wav", "scientist"),
        ("valve_spanish/sound/barney/ba_later.wav", "barney"),
        ("audio/vo/en/Niko/niko_001.wav", "Niko"),
        ("sound/hola.wav", ""),
    ],
)
def test_guess_character(line_id, expected):
    assert pj.guess_character(line_id) == expected


def test_only_lines_in_view_are_processed(tmp_path: Path):
    project = _project(tmp_path, [f"line {n}" for n in range(6)])
    _engine(tmp_path, project).run(_opts(FakeAI(), only=[5, 3]))
    assert [bool(line.translation) for line in project.lines] == [False, False, False, True, False, True]


def test_clip_translates_only_part_and_caches_separately(tmp_path: Path):
    project = _project(tmp_path, ["Hello Gordon, welcome"])
    line = project.lines[0]
    line.duration = 30.0  # el "audio" de prueba es texto: cada carácter cuenta como un segundo
    pj.set_clip(line, 6, 12)  # "Gordon"
    _engine(tmp_path, project).run(_opts(FakeAI()))
    assert line.original == "Gordon" and line.translation == "[es-419] Gordon"

    pj.set_clip(line, None, None)  # cambiar el tramo reinicia la línea y no reutiliza la transcripción vieja
    assert line.status == pj.PENDING and not line.original
    _engine(tmp_path, project).run(_opts(FakeAI()))
    assert line.original == "Hello Gordon, welcome"


def test_set_clip_validates():
    line = pj.Line("a", duration=5.0)
    with pytest.raises(ValueError):
        pj.set_clip(line, 3, 2)
    with pytest.raises(ValueError):
        pj.set_clip(line, 6, None)
    line.original, line.status = "x", pj.TRANSCRIBED
    pj.set_clip(line, None, None)  # mismo tramo: no borra nada
    assert line.original == "x"


@pytest.mark.parametrize("text,expected", [("", None), ("83.5", 83.5), ("1:23.5", 83.5), ("0:01:05", 65.0), ("2,5", 2.5)])
def test_parse_time(text, expected):
    assert pj.parse_time(text) == expected
    if expected is not None:
        assert pj.parse_time(pj.format_time(expected)) == pytest.approx(expected)


@pytest.mark.parametrize("bad", ["abc", "1:xx", "-3"])
def test_parse_time_rejects(bad):
    with pytest.raises(ValueError):
        pj.parse_time(bad)


def test_export_txt_and_csv(tmp_path: Path):
    lines = [
        pj.Line("sound/barney/a.wav", character="barney", original="Catch me later.", translation="Nos vemos."),
        pj.Line("sound/sci/b.wav", original="Hello", clip_start=0.5, clip_end=2.0),
    ]
    pj.export(tmp_path / "sub" / "r.txt", lines)  # crea la carpeta si no existe
    txt = (tmp_path / "sub" / "r.txt").read_text("utf-8-sig")
    assert "sound/barney/a.wav  [barney]" in txt and "→ Nos vemos." in txt and "(0:00.5 - 0:02.0)" in txt
    pj.export(tmp_path / "r.csv", lines)
    assert "Nos vemos." in (tmp_path / "r.csv").read_text("utf-8-sig")


def test_ollama_opens_itself_when_closed(monkeypatch, tmp_path: Path):
    from revoz.core import providers as pv

    app = tmp_path / "Programs" / "Ollama" / "ollama app.exe"
    app.parent.mkdir(parents=True)
    app.write_bytes(b"MZ")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    started = []
    calls = {"n": 0}

    def fake_get(url, headers, timeout=15):
        calls["n"] += 1
        if not started:  # cerrado hasta que se abre
            raise pv.ProviderError("no conecta", retry=True)
        return {"models": [{"name": "qwen2.5:3b"}]}

    monkeypatch.setattr(pv, "get_json", fake_get)
    monkeypatch.setattr(pv.subprocess, "Popen", lambda args, **kw: started.append(args))
    monkeypatch.setattr(pv.sys, "platform", "win32")
    assert pv.Ollama().models("") == ["qwen2.5:3b"]
    assert started and started[0][0].endswith("ollama app.exe")


class FakeTTS:
    """Voz falsa: escribe un WAV mudo que dura 0.1 s por letra (dividido por la velocidad)."""

    name = "Fake"
    needs_key = False

    def __init__(self):
        self.calls = []

    def synthesize(self, jobs, key=""):
        import wave

        for j in jobs:
            self.calls.append((j.voice_id, j.text) if j.speed == 1.0 else (j.voice_id, j.text, j.speed))
            j.out.parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(j.out), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes(b"\0\0" * int(1600 * len(j.text) / j.speed))


def _voice_project(tmp_path: Path, texts: list[str], seconds: float = 2.0):
    import wave

    lines = []
    for n, text in enumerate(texts):
        f = tmp_path / "game" / "sound" / ("barney" if n % 2 else "scientist") / f"{n}.wav"
        f.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(f), "wb") as w:  # audio "original" del juego: 11025 Hz mono
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(11025)
            w.writeframes(b"\0\0" * int(11025 * seconds))
        rel = f.relative_to(tmp_path / "game").as_posix()
        lines.append(pj.Line(rel, str(f), seconds, character=pj.guess_character(rel), original=text, translation=text, status=pj.TRANSLATED))
    return pj.Project("1", "Juego", lines=lines, default_voice="Fake:normal", voices={"barney": "Fake:barney"})


def _voice_opts(tts, tmp_path, **kw):
    return Options(transcribe=False, translate=False, voice=True, tts={"Fake": tts}, dub_dir=tmp_path / "voz", **kw)


def test_voice_step_generates_fitted_audio_with_game_format(tmp_path: Path):
    from revoz.core import audio, fit

    project = _voice_project(tmp_path, ["hola", "x" * 60])  # 0.4 s entra; 6 s no entra en 2 s ni hablando rápido y acelerando
    tts = FakeTTS()
    s = _engine(tmp_path, project).run(_voice_opts(tts, tmp_path))
    short, long = project.lines
    assert (s.voiced, s.too_long) == (2, 1)
    assert tts.calls == [("normal", "hola"), ("barney", "x" * 60), ("barney", "x" * 60, 1.5)]  # cada personaje con su voz
    assert Path(short.dub_path).name == "0.wav" and short.dub_fits
    assert fit.audio_format(Path(short.dub_path)) == (11025, "1 channels")  # mismo formato que el juego
    assert long.dub_fits is False and audio.duration(Path(long.dub_path)) == pytest.approx(6.0 / 1.5 / 1.35, abs=0.05)


def test_voice_speaks_faster_before_stretching(tmp_path: Path):
    project = _voice_project(tmp_path, ["x" * 30])  # 3 s en 2 s: la voz habla 1.5x y entra sin acelerar el audio
    tts = FakeTTS()
    _engine(tmp_path, project).run(_voice_opts(tts, tmp_path))
    assert tts.calls[-1] == ("normal", "x" * 30, 1.5) and project.lines[0].dub_fits


def test_pronunciation_dictionary_changes_what_the_voice_says(tmp_path: Path):
    project = _voice_project(tmp_path, ["Hola Gordon, bienvenido a Black Mesa"])
    project.pronunciations = {"gordon": "Górdon", "Black Mesa": "Blak Mésa"}
    tts = FakeTTS()
    _engine(tmp_path, project).run(_voice_opts(tts, tmp_path))
    assert tts.calls[0][1] == "Hola Górdon, bienvenido a Blak Mésa"
    assert project.lines[0].translation == "Hola Gordon, bienvenido a Black Mesa"  # el texto traducido no cambia


def test_voice_is_not_regenerated_unless_text_or_voice_changes(tmp_path: Path):
    project = _voice_project(tmp_path, ["hola"])
    tts = FakeTTS()
    _engine(tmp_path, project).run(_voice_opts(tts, tmp_path))
    _engine(tmp_path, project).run(_voice_opts(tts, tmp_path))
    assert len(tts.calls) == 1  # nada cambió: no se vuelve a generar

    project.lines[0].translation = "chau"
    _engine(tmp_path, project).run(_voice_opts(tts, tmp_path))
    project.default_voice = "Fake:otra"
    _engine(tmp_path, project).run(_voice_opts(tts, tmp_path))
    assert tts.calls[1:] == [("normal", "chau"), ("otra", "chau")]


def test_voice_without_chosen_voice_marks_error(tmp_path: Path):
    project = _voice_project(tmp_path, ["hola"])
    project.default_voice = ""
    s = _engine(tmp_path, project).run(_voice_opts(FakeTTS(), tmp_path))
    assert s.errors == 1 and "voz" in project.lines[0].error


def test_export_audio_keeps_game_paths(tmp_path: Path):
    project = _voice_project(tmp_path, ["hola", "chau"])
    _engine(tmp_path, project).run(_voice_opts(FakeTTS(), tmp_path))
    n = pj.export_audio(tmp_path / "salida", project.lines)
    assert n == 2 and (tmp_path / "salida" / "sound" / "barney" / "1.wav").is_file()


def test_voice_is_picked_automatically_when_none_chosen(tmp_path: Path):
    from revoz.core.tts import Voice

    project = _voice_project(tmp_path, ["hola"])
    project.default_voice, project.voices = "", {}
    tts = FakeTTS()
    tts.voices = lambda key="": [Voice("Windows", "en", "Zira", "en-US", "Female"), Voice("Windows", "es", "Sabina", "es-ES", "Female"), Voice("Windows", "mx", "Raul", "es-MX", "Male")]
    opts = _voice_opts(tts, tmp_path)
    opts.tts = {"Windows": tts}
    _engine(tmp_path, project).run(opts)
    assert project.default_voice == "Windows:mx"
