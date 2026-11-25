"""Instalar en el juego con backup, restaurar, actualizaciones del juego y vuelta atrás automática."""

import wave
from pathlib import Path

import pytest

from revoz.core import audio, install
from revoz.core import project as pj


def _wav(path: Path, seconds: float, rate: int = 22050, width: int = 1) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes((b"\x80" if width == 1 else b"\0\0") * int(rate * seconds))
    return path


@pytest.fixture
def game(tmp_path: Path):
    root = tmp_path / "Half-Life"
    lines = []
    for n in range(3):
        original = _wav(root / "valve" / "sound" / "barney" / f"ba_{n}.wav", 1.0)
        dub = _wav(tmp_path / "dub" / f"{n}.wav", 0.5, rate=44100, width=2)
        lines.append(pj.Line(f"valve/sound/barney/ba_{n}.wav", path=str(original), duration=1.0, dub_path=str(dub)))
    return root, lines, tmp_path / "backups" / "70"


def _params(path: Path):
    with wave.open(str(path)) as w:
        return w.getframerate(), w.getnchannels(), w.getsampwidth()


def test_install_keeps_the_game_format_and_restore_brings_back_the_original(game):
    root, lines, backups = game
    before = [Path(line.path).read_bytes() for line in lines]
    result = install.install(lines, root, backups)
    assert result.done == 3 and install.installed(backups) == 3
    target = Path(lines[0].path)
    assert _params(target) == (22050, 1, 1)  # mismo formato que el original del juego (8 bits, 22 kHz, mono)
    assert audio.duration(target) == pytest.approx(0.5, abs=0.02)  # pero con la voz nueva
    assert (backups / "files" / "valve/sound/barney/ba_0.wav").read_bytes() == before[0]

    result = install.restore(root, backups)
    assert result.done == 3 and [Path(line.path).read_bytes() for line in lines] == before
    assert not backups.exists()  # sin nada instalado no queda basura


def test_installing_twice_keeps_the_true_original(game):
    root, lines, backups = game
    before = Path(lines[0].path).read_bytes()
    install.install(lines, root, backups)
    install.install(lines, root, backups)  # reinstalar no tiene que guardar el doblaje como si fuera el original
    install.restore(root, backups)
    assert Path(lines[0].path).read_bytes() == before


def test_game_update_is_detected_and_not_overwritten(game):
    root, lines, backups = game
    install.install(lines, root, backups)
    updated = _wav(Path(lines[1].path), 2.0)  # la tienda actualizó este audio
    assert install.changed(root, backups) == ["valve/sound/barney/ba_1.wav"]
    result = install.restore(root, backups)
    assert result.done == 2 and result.changed == ["valve/sound/barney/ba_1.wav"]
    assert audio.duration(updated) == pytest.approx(2.0)  # el audio nuevo del juego quedó como estaba


def test_reinstall_after_update_backs_up_the_new_original(game):
    root, lines, backups = game
    install.install(lines, root, backups)
    new_original = _wav(Path(lines[1].path), 2.0).read_bytes()
    install.install(lines, root, backups)  # reinstalar después de la actualización
    install.restore(root, backups)
    assert Path(lines[1].path).read_bytes() == new_original


def test_never_touches_files_outside_the_game(game, tmp_path):
    root, lines, backups = game
    outside = _wav(tmp_path / "otra_carpeta" / "x.wav", 1.0)
    lines[0].path = str(outside)
    result = install.install(lines, root, backups)
    assert result.done == 2 and result.skipped[0][0] == lines[0].id
    assert audio.duration(outside) == pytest.approx(1.0)


def test_failure_halfway_rolls_back(game, monkeypatch):
    root, lines, backups = game
    before = [Path(line.path).read_bytes() for line in lines]
    real = install.fit.encode_like
    calls = []

    def flaky(src, original, dst):
        calls.append(dst)
        if len(calls) == 3:
            raise OSError("disco lleno")
        real(src, original, dst)

    monkeypatch.setattr(install.fit, "encode_like", flaky)
    with pytest.raises(OSError):
        install.install(lines, root, backups)
    assert [Path(line.path).read_bytes() for line in lines] == before  # los dos que alcanzó a instalar volvieron
