"""Juegos de otras tiendas (Epic, GOG) y agregados a mano. Solo lectura de los datos de cada tienda.

Ids: Steam usa el appid ("12210"); el resto lleva prefijo ("epic:Fortnite", "gog:1207658924", "manual:3f2a…").
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path

from revoz.core import steam
from revoz.core.steam import Game

EPIC_MANIFESTS = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"


def list_epic(folder: Path = EPIC_MANIFESTS) -> list[Game]:
    games = []
    for f in folder.glob("*.item"):
        try:
            d = json.loads(f.read_text("utf-8"))
            app, name, path = d["AppName"], d["DisplayName"], Path(d["InstallLocation"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        # Saltea instalaciones a medias y DLC (los DLC apuntan a su juego principal).
        if d.get("bIsIncompleteInstall") or d.get("MainGameAppName", app) != app or not path.is_dir():
            continue
        games.append(Game(f"epic:{app}", name, path, str(d.get("AppVersionString", "")), None, "Epic"))
    return games


def gog_game(values: dict) -> Game | None:
    """Arma un Game a partir de los valores de registro de un juego de GOG."""
    name, path, gid = values.get("gameName"), values.get("path"), values.get("gameID")
    if not (name and path and gid) or values.get("dependsOn"):  # dependsOn = es un DLC
        return None
    path = Path(path)
    if not path.is_dir():
        return None
    return Game(f"gog:{gid}", name, path, str(values.get("ver", "")), None, "GOG")


def list_gog() -> list[Game]:
    if sys.platform != "win32":
        return []
    import winreg

    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\GOG.com\Games")
    except OSError:
        return []
    games = []
    with root:
        i = 0
        while True:
            try:
                sub = winreg.EnumKey(root, i)
            except OSError:
                break
            i += 1
            values = {}
            try:
                with winreg.OpenKey(root, sub) as k:
                    j = 0
                    while True:
                        try:
                            name, value, _ = winreg.EnumValue(k, j)
                        except OSError:
                            break
                        values[name] = value
                        j += 1
            except OSError:
                continue
            if game := gog_game(values):
                games.append(game)
    return games


# --- juegos agregados a mano ---


def _manual_id(path: Path) -> str:
    return "manual:" + hashlib.sha1(str(path.resolve()).lower().encode()).hexdigest()[:12]


def _read_manual(file: Path) -> list[dict]:
    try:
        data = json.loads(file.read_text("utf-8"))
        return [d for d in data if isinstance(d, dict) and isinstance(d.get("path"), str)]
    except (OSError, ValueError, TypeError):
        return []


def _write_manual(file: Path, entries: list[dict]) -> None:
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(entries, indent=1, ensure_ascii=False), "utf-8")


def list_manual(file: Path) -> list[Game]:
    games = []
    for d in _read_manual(file):
        path = Path(d["path"])
        if path.is_dir():
            games.append(Game(_manual_id(path), str(d.get("name") or path.name), path, "", None, "Manual"))
    return games


def add_manual(file: Path, path: Path, name: str | None = None) -> Game:
    """Agrega un juego por su carpeta o por su .exe (se usa la carpeta del .exe)."""
    if path.is_file():
        path = path.parent
    if not path.is_dir():
        raise ValueError("La carpeta no existe")
    path = path.resolve()
    if path == Path(path.anchor):
        raise ValueError("Elegí la carpeta del juego, no la raíz de un disco")
    entries = _read_manual(file)
    if any(_manual_id(Path(d["path"])) == _manual_id(path) for d in entries):
        raise ValueError("Ese juego ya está en la biblioteca")
    name = (name or path.name).strip()
    entries.append({"name": name, "path": str(path)})
    _write_manual(file, entries)
    return Game(_manual_id(path), name, path, "", None, "Manual")


def remove_manual(file: Path, game_id: str) -> None:
    _write_manual(file, [d for d in _read_manual(file) if _manual_id(Path(d["path"])) != game_id])


# --- comprobar que una carpeta es el mismo juego ---

# Archivos y carpetas que traen muchos juegos distintos (motor, tienda, redistribuibles):
# no sirven para reconocer un juego, así que no cuentan en la comparación.
_GENERIC = {
    "engine", "redist", "_commonredist", "directx", "vcredist", "support", "monobleedingedge",
    "unityplayer.dll", "unitycrashhandler64.exe", "unitycrashhandler32.exe", "baselib.dll", "gameassembly.dll",
    "steam_api.dll", "steam_api64.dll", "steam_appid.txt", "steam_emu.ini",
    "eossdk-win64-shipping.dll", "eossdk-win32-shipping.dll", ".egstore",
    "galaxy.dll", "galaxy64.dll", "unins000.exe", "unins000.dat", "desktop.ini", "thumbs.db",
}  # fmt: skip
SAME_GAME_MIN = 0.5  # ponytail: umbral fijo; copias con mods o con idiomas quitados quedan arriba, otro juego queda cerca de 0


def fingerprint(root: Path, depth: int = 3) -> set[str]:
    """Rutas relativas (en minúsculas) de los primeros niveles de la carpeta, sin lo genérico."""
    found: set[str] = set()

    def walk(folder: Path, prefix: str, level: int) -> None:
        try:
            entries = list(os.scandir(folder))
        except OSError:
            return
        for e in entries:
            name = e.name.lower()
            if name in _GENERIC or name.startswith("goggame-"):
                continue
            rel = f"{prefix}{name}"
            found.add(rel)
            if level < depth and e.is_dir(follow_symlinks=False):
                walk(Path(e.path), rel + "/", level + 1)

    walk(root, "", 1)
    return found


def same_game_score(reference: Path, candidate: Path) -> float:
    """Qué parte de los archivos del juego de referencia está también en la otra carpeta (0 a 1)."""
    ref = fingerprint(reference)
    return len(ref & fingerprint(candidate)) / len(ref) if ref else 0.0


STORE_ORDER = ["Steam", "Epic", "GOG", "Folder", "Manual"]


def list_folder_games(folders: list[Path]) -> list[Game]:
    """Juegos dentro de carpetas que eligió el usuario (ej. D:\\Juegos): cada subcarpeta con un .exe es un juego.
    Sirve para juegos que ninguna tienda registra."""
    games = []
    for folder in folders:
        try:
            subdirs = [d for d in folder.iterdir() if d.is_dir() and not d.name.startswith(".")]
        except OSError:
            continue  # disco desconectado o sin permiso
        for d in subdirs:
            if _has_exe(d):
                games.append(Game("folder:" + _manual_id(d).split(":", 1)[1], d.name, d.resolve(), "", None, "Folder"))
    return games


def _has_exe(folder: Path, depth: int = 3) -> bool:
    try:
        for e in os.scandir(folder):
            if e.is_file() and e.name.lower().endswith(".exe") and e.name.lower() not in _GENERIC:
                return True
            if depth > 1 and e.is_dir(follow_symlinks=False) and _has_exe(Path(e.path), depth - 1):
                return True
    except OSError:
        pass
    return False


def group_key(name: str) -> str:
    """Nombre normalizado: sin mayúsculas, ™/®/© ni signos. "Grand Theft Auto V™" == "grand theft auto v"."""
    return " ".join(re.findall(r"[^\W_]+", re.sub(r"[™®©]", "", name.lower())))


def group_games(games: list[Game]) -> list[list[Game]]:
    """Junta el mismo juego instalado desde varias tiendas (solo si el nombre coincide exacto).
    Ante la duda quedan separados: "GTA V" y "GTA V Enhanced" son juegos distintos para Revoz."""
    groups: dict[str, list[Game]] = {}
    for g in games:
        groups.setdefault(group_key(g.name), []).append(g)
    for versions in groups.values():
        versions.sort(key=lambda g: STORE_ORDER.index(g.store) if g.store in STORE_ORDER else len(STORE_ORDER))
    return list(groups.values())


def all_games(manual_file: Path, folders: list[Path] | None = None) -> list[Game]:
    """Todas las tiendas juntas. Si otra tienda ya tiene esa carpeta, gana la primera
    (Steam > Epic > GOG > carpetas de juegos > agregados a mano).
    Dentro de una misma tienda no se filtra: en Steam varios juegos comparten carpeta (Half-Life, Counter-Strike)."""
    sources = []
    root = steam.find_steam()
    if root:
        sources.append(lambda: steam.list_games(root))
    sources += [list_epic, list_gog, lambda: list_folder_games(folders or []), lambda: list_manual(manual_file)]
    games: list[Game] = []
    seen: set[str] = set()
    for source in sources:
        try:
            found = source()
        except (OSError, ValueError):
            continue  # una tienda rota no tira abajo la biblioteca
        new = [g for g in found if str(g.path.resolve()).lower() not in seen]
        games += new
        seen.update(str(g.path.resolve()).lower() for g in new)
    return sorted(games, key=lambda g: g.name.lower())
