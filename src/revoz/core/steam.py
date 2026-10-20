"""Detección de juegos instalados en Steam (solo lectura)."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

# Herramientas de Steam que no son juegos.
_NOT_GAMES = {"228980"}  # Steamworks Common Redistributables

_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])')


@dataclass(frozen=True)
class Game:
    appid: str
    name: str
    path: Path
    buildid: str
    cover: Path | None
    store: str = "Steam"


def parse_vdf(text: str) -> dict:
    """Parsea el formato KeyValues de Valve (.vdf / .acf) a dicts anidados."""
    root: dict = {}
    stack = [root]
    key: str | None = None
    for m in _TOKEN.finditer(text):
        string, brace = m.groups()
        if brace == "{":
            if key is None:
                raise ValueError("VDF inválido: '{' sin clave")
            child: dict = {}
            stack[-1][key] = child
            stack.append(child)
            key = None
        elif brace == "}":
            if len(stack) == 1:
                raise ValueError("VDF inválido: '}' de más")
            stack.pop()
        else:
            value = re.sub(r"\\(.)", r"\1", string)
            if key is None:
                key = value
            else:
                stack[-1][key] = value
                key = None
    if len(stack) != 1:
        raise ValueError("VDF inválido: falta '}'")
    return root


def find_steam() -> Path | None:
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
                return Path(winreg.QueryValueEx(k, "SteamPath")[0])
        except OSError:
            pass
        default = Path(r"C:\Program Files (x86)\Steam")
        return default if default.exists() else None
    default = Path.home() / ".steam" / "steam"
    return default if default.exists() else None


def _cover(steam_root: Path, appid: str) -> Path | None:
    cache = steam_root / "appcache" / "librarycache"
    for p in (cache / appid / "library_600x900.jpg", cache / f"{appid}_library_600x900.jpg"):
        if p.is_file():
            return p
    return None


def list_games(steam_root: Path) -> list[Game]:
    folders = parse_vdf((steam_root / "steamapps" / "libraryfolders.vdf").read_text("utf-8", "replace"))
    games = []
    for lib in folders.get("libraryfolders", {}).values():
        if not isinstance(lib, dict) or "path" not in lib:
            continue
        steamapps = Path(lib["path"]) / "steamapps"
        for acf in steamapps.glob("appmanifest_*.acf"):
            try:
                app = parse_vdf(acf.read_text("utf-8", "replace"))["AppState"]
                appid, name, installdir = app["appid"], app["name"], app["installdir"]
            except (OSError, ValueError, KeyError):
                continue  # manifiesto roto: lo salteamos
            path = steamapps / "common" / installdir
            if appid in _NOT_GAMES or not path.is_dir():
                continue
            games.append(Game(appid, name, path, app.get("buildid", ""), _cover(steam_root, appid)))
    return sorted(games, key=lambda g: g.name.lower())
