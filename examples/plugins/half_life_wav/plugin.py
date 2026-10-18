"""Plugin de ejemplo para juegos con audio WAV suelto (Half-Life).

Sirve de plantilla: un plugin real para audio cifrado haría lo mismo,
pero descifrando y reempaquetando el contenedor del juego.
"""

import shutil
from pathlib import Path

from revoz.core.plugins import GamePlugin

SOUND_DIR = Path("valve") / "sound"
BACKUP_DIR = ".revoz_backup"


def _inside(game_path: Path, audio_id: str) -> Path:
    # Nunca tocar nada fuera de la carpeta del juego (ids como "../../Windows").
    target = (game_path / audio_id).resolve()
    if not target.is_relative_to(game_path.resolve()):
        raise ValueError(f"ruta fuera del juego: {audio_id}")
    return target


class Plugin(GamePlugin):
    def list_audio(self, game_path: Path) -> list[str]:
        root = game_path / SOUND_DIR
        return sorted(p.relative_to(game_path).as_posix() for p in root.rglob("*.wav"))

    def extract(self, game_path: Path, audio_id: str, out_dir: Path) -> Path:
        out = out_dir / audio_id
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(_inside(game_path, audio_id), out)
        return out

    def replace(self, game_path: Path, files: dict[str, Path]) -> None:
        for audio_id, new_wav in files.items():
            target = _inside(game_path, audio_id)
            backup = game_path / BACKUP_DIR / audio_id
            if not backup.exists():  # el backup guarda siempre el original
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup)
            shutil.copy2(new_wav, target)
