import shutil
import zipfile
from pathlib import Path

import pytest

from revoz.core import plugins, providers

EXAMPLE = Path(__file__).parent.parent / "examples" / "plugins" / "half_life_wav"


def _copy_example(root: Path) -> Path:
    dest = root / "half_life_wav"
    shutil.copytree(EXAMPLE, dest)
    return dest


def test_discover_does_not_run_code_and_reports_errors(tmp_path: Path):
    _copy_example(tmp_path)
    bad = tmp_path / "roto"
    bad.mkdir()
    (bad / "plugin.toml").write_text('name = "x"\nkind = "otra cosa"')
    (bad / "plugin.py").write_text("raise SystemExit('no debería ejecutarse')")

    found, errors = plugins.discover(tmp_path)
    assert [(p.id, p.kind, p.appids) for p in found] == [("half_life_wav", "game", ("70",))]
    assert len(errors) == 1 and errors[0].startswith("roto:")


def test_load_requires_trust_and_detects_changes(tmp_path: Path):
    folder = _copy_example(tmp_path)
    trust_file = tmp_path / "trusted.json"
    [info], _ = plugins.discover(tmp_path)
    trusted = plugins.load_trusted(trust_file)

    with pytest.raises(plugins.PluginError):
        plugins.load(info, trusted)  # no aprobado

    plugins.trust(trust_file, trusted, info)
    assert isinstance(plugins.load(info, plugins.load_trusted(trust_file)), plugins.GamePlugin)

    with open(folder / "plugin.py", "a") as f:
        f.write("\n# cambio\n")
    with pytest.raises(plugins.PluginError):
        plugins.load(info, trusted)  # modificado después de aprobarlo


def test_example_plugin_replace_with_backup_and_path_guard(tmp_path: Path):
    plugins_root, game = tmp_path / "plugins", tmp_path / "game"
    _copy_example(plugins_root)
    [info], _ = plugins.discover(plugins_root)
    trusted: dict[str, str] = {}
    plugins.trust(tmp_path / "t.json", trusted, info)
    plugin = plugins.load(info, trusted)

    wav = game / "valve" / "sound" / "vox" / "hola.wav"
    wav.parent.mkdir(parents=True)
    wav.write_bytes(b"original")
    new = tmp_path / "nuevo.wav"
    new.write_bytes(b"traducido")

    assert plugin.list_audio(game) == ["valve/sound/vox/hola.wav"]
    plugin.replace(game, {"valve/sound/vox/hola.wav": new})
    plugin.replace(game, {"valve/sound/vox/hola.wav": new})  # segunda vez no pisa el backup
    assert wav.read_bytes() == b"traducido"
    assert (game / ".revoz_backup" / "valve/sound/vox/hola.wav").read_bytes() == b"original"
    with pytest.raises(ValueError):
        plugin.replace(game, {"../afuera.wav": new})


def test_plugin_must_inherit_base(tmp_path: Path):
    folder = tmp_path / "malo"
    folder.mkdir()
    (folder / "plugin.toml").write_text('name = "Malo"\nkind = "provider"')
    (folder / "plugin.py").write_text("class Plugin: pass")
    [info], _ = plugins.discover(tmp_path)
    trusted: dict[str, str] = {}
    plugins.trust(tmp_path / "t.json", trusted, info)
    with pytest.raises(plugins.PluginError, match="Provider"):
        plugins.load(info, trusted)


def test_install_from_folder_and_zip(tmp_path: Path):
    root = tmp_path / "plugins"
    info = plugins.install(EXAMPLE, root)
    assert info.id == "half_life_wav" and (root / "half_life_wav" / "plugin.py").is_file()
    with pytest.raises(plugins.PluginError, match="ya hay"):
        plugins.install(EXAMPLE, root)

    # zip con el plugin dentro de una subcarpeta (lo típico al comprimir una carpeta)
    archive = tmp_path / "otro.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("mi_plugin/plugin.toml", 'name = "Otro"\nkind = "game"\nappids = ["10"]')
        zf.writestr("mi_plugin/plugin.py", "")
    assert plugins.install(archive, root).id == "mi_plugin"
    assert not [p for p in root.iterdir() if p.name.startswith(".")]  # sin restos temporales


@pytest.mark.parametrize(
    "files,error",
    [
        ({"../afuera/plugin.toml": "x"}, "ruta peligrosa"),
        ({"leeme.txt": "x"}, "plugin.toml"),
        ({"plugin.toml": 'name = "x"\nkind = "game"', "plugin.py": ""}, "appid"),
        ({"plugin.toml": 'name = "x"\nkind = "game"\nappids = ["1"]'}, "plugin.py"),
    ],
)
def test_install_rejects_bad_zips(tmp_path: Path, files, error):
    archive = tmp_path / "malo.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    root = tmp_path / "plugins"
    with pytest.raises(plugins.PluginError, match=error):
        plugins.install(archive, root)
    assert list(root.iterdir()) == []
    assert not (tmp_path / "afuera").exists()


def test_supports():
    assert providers.supports(None, "ja")
    assert providers.supports({"en-us", "es", "pt-br"}, "pt")
    assert not providers.supports({"en-us", "de"}, "es")


@pytest.mark.parametrize(
    "provider,response,expected",
    [
        (providers.DeepL(), [{"language": "ES", "name": "Spanish"}, {"language": "PT-BR"}], {"es", "pt-br"}),
        (providers.ElevenLabs(), [{"languages": [{"language_id": "es"}]}, {"languages": [{"language_id": "en"}]}], {"es", "en"}),
        (providers.Google(), {"data": {"languages": [{"language": "es"}, {"language": "fr"}]}}, {"es", "fr"}),
        (providers.Anthropic(), {"data": []}, None),
        (providers.Gemini(), {"models": []}, None),
        *[(p, {"data": []}, None) for p in providers.BUILTIN if isinstance(p, providers.OpenAICompatible)],
    ],
)
def test_providers_parse_and_keep_key_out_of_url(monkeypatch, provider, response, expected):
    calls = []

    def fake_get_json(url, headers):
        calls.append((url, headers))
        return response

    monkeypatch.setattr(providers, "get_json", fake_get_json)
    assert provider.languages("CLAVE-SECRETA") == expected
    url, headers = calls[0]
    assert url.startswith("https://") and "CLAVE-SECRETA" not in url
    assert any("CLAVE-SECRETA" in v for v in headers.values())


def test_builtin_provider_names_are_unique():
    names = [p.name for p in providers.BUILTIN]
    assert len(names) == len(set(names))


def test_get_json_rejects_http():
    with pytest.raises(ValueError):
        providers.get_json("http://example.com", {})
