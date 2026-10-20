import wave
from pathlib import Path

import pytest

from revoz.core import project as pj


def _wav(path: Path, seconds: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\0\0" * int(8000 * seconds))


def test_build_lines_only_single_line_formats(tmp_path: Path):
    _wav(tmp_path / "vox" / "hola.wav", 2)
    (tmp_path / "banco.bnk").write_bytes(b"BKHD" + b"\0" * 20)  # banco con muchas líneas: no es una línea
    (tmp_path / "x.txt").write_text("nada")
    lines = pj.build_lines(tmp_path)
    assert [(line.id, round(line.duration, 2)) for line in lines] == [("vox/hola.wav", 2.0)]


def test_build_lines_from_plugin(tmp_path: Path):
    class P:
        def list_audio(self, game_path):
            return ["speech/niko_001", "speech/roman_001"]

    assert [line.id for line in pj.build_lines(tmp_path, P())] == ["speech/niko_001", "speech/roman_001"]


def test_update_advances_status():
    line = pj.Line("a")
    pj.update(line, "character", "Niko")
    assert line.status == pj.PENDING
    pj.update(line, "original", "Hello cousin")
    assert line.status == pj.TRANSCRIBED
    pj.update(line, "translation", "Hola primo")
    assert line.status == pj.TRANSLATED
    line.status = pj.REVIEWED
    pj.update(line, "translation", "Hola, primo")
    assert line.status == pj.REVIEWED  # editar no "desrevisa"


def test_progress_and_estimate():
    lines = [
        pj.Line("a", duration=60, original="x" * 100, status=pj.REVIEWED),
        pj.Line("b", duration=60, status=pj.TRANSLATED),
        pj.Line("c", duration=None),
    ]
    assert pj.progress(lines) == {pj.TRANSCRIBED: 2, pj.TRANSLATED: 2, pj.REVIEWED: 1}
    e = pj.estimate(lines, usd_per_minute=0.5, usd_per_million_chars=20)
    assert (e.minutes, e.without_duration) == (2, 1)
    assert e.chars == 100 + 60 * pj.CHARS_PER_SECOND  # real si hay texto; estimado si no
    assert e.cost == pytest.approx(2 * 0.5 + e.chars / 1e6 * 20)


def test_save_load_roundtrip(tmp_path: Path):
    p = pj.Project("12210", "GTA IV", "es-419", "OpenAI", [pj.Line("a", duration=1.5, translation="hola")])
    pj.save(tmp_path / "p.json", p)
    assert pj.load(tmp_path / "p.json") == p
    assert pj.load(tmp_path / "no.json") is None
    (tmp_path / "roto.json").write_text("{")
    assert pj.load(tmp_path / "roto.json") is None


def test_csv_roundtrip_merge_and_formula_guard(tmp_path: Path):
    lines = [pj.Line("a", original="=SUMA(1)"), pj.Line("b", translation="ya traducida", status=pj.TRANSLATED)]
    pj.export_csv(tmp_path / "x.csv", lines)
    assert "'=SUMA(1)" in (tmp_path / "x.csv").read_text("utf-8-sig")

    # otra persona tradujo "a" y dejó "b" vacía en su copia
    (tmp_path / "lote.csv").write_text(
        "id,personaje,original,traduccion,estado\na,Niko,=SUMA(1),Hola,\nb,,,,\nz,,,no existe,\n", encoding="utf-8"
    )
    fresh = [pj.Line("a", original="=SUMA(1)"), pj.Line("b", translation="ya traducida", status=pj.TRANSLATED)]
    assert pj.import_csv(tmp_path / "lote.csv", fresh) == 1
    assert (fresh[0].character, fresh[0].translation, fresh[0].status) == ("Niko", "Hola", pj.TRANSLATED)
    assert fresh[1].translation == "ya traducida"  # una celda vacía no borra
