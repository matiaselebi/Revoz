# Revoz

**[English](#english) · [Español](#español)**

---

## English

Revoz is an open source desktop tool for translating the **voices** of video games with the AI service of your choice.

It scans your Steam library, tells you which games have audio that can be translated, and lets you plug in your own API key for any translation or dubbing service. The community extends it with **plugins**: one plugin per game (or engine) that knows how to extract and replace that game's audio, and plugins that add new AI services.

> **Status: early development.** Library scanning, the plugin system, API key storage and language checks work. The actual translation pipeline (extract → transcribe → translate → synthesize → replace) is not implemented yet.

### What Revoz is (and is not)

- Revoz is **only a tool**. It does not include, host, distribute or link game files, audio, encryption keys or translations.
- You use **your own** API key. Revoz does not ship or pay for any AI service.
- What you generate with Revoz is your responsibility. Respect the terms of the games you own and the rights of their voice actors.

### Features

- Detects installed Steam games, with their covers.
- Scans each game's files automatically and shows a badge on its cover:

| Badge | Meaning |
|---|---|
| ✓ green | Readable audio (WAV, Ogg, MP3, FLAC, FMOD, Wwise): easy to translate. Or a plugin supports the game. |
| ! yellow | Audio packed inside unencrypted containers (Unity bundles, Unreal `.pak`, Valve `.vpk`, RAGE `.rpf`). Needs a plugin to extract it. |
| ✗ red | Encrypted audio (encrypted RAGE/Unreal containers, or audio files that don't match their format), or no audio found. |

- Scanning is **read-only**: Revoz reads only file headers and never modifies your games. Results are cached and refreshed when Steam updates a game.
- API keys are stored in the operating system's secure store (Windows Credential Manager), never in plain text.
- Target language selector with a **Check language** button that asks the chosen service whether it supports that language and validates your key.
- Built-in services: OpenAI, Anthropic, DeepL, ElevenLabs, Google Translate. More can be added with plugins.

### Requirements

- Windows 10/11 (Linux may work but is untested)
- Python 3.11 or newer
- Steam

### Running from source

```bash
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\revoz
```

Run the tests:

```bash
.venv\Scripts\python -m pytest
```

### Building the .exe

Double-click `build.bat` (or run it from a terminal). It creates `dist\Revoz.exe`, a single file that runs without Python installed. The `plugins` folder is created next to the `.exe` the first time it runs.

### Plugins

#### Where plugins go

Plugins live in the `plugins` folder **next to Revoz** (next to `Revoz.exe`, or in the project root when running from source). Each plugin is a folder inside it.

The easiest way to manage them is the **Plugins** tab:

- **Add from .zip** or **Add folder**: installs a plugin you downloaded. Nothing runs until you approve it.
- **Remove**: sends the plugin folder to the Recycle Bin.
- **Reload**: picks up plugins you copied by hand.
- The **Status** column shows whether each plugin is active, not approved, or failed to load (with the reason).

The list of approved plugins is stored in your user profile (`%APPDATA%\Revoz`), not in the Revoz folder, so a downloaded copy of Revoz can't bring plugins that are already approved.

#### Security

Plugins are Python code that runs on your PC. Revoz will **not** run a plugin until you approve it. When it finds a new plugin, it shows its name, author and folder and asks whether to enable it. If any file of an approved plugin changes, Revoz asks again. Only install plugins from people you trust.

#### Plugin structure

```
plugins/
└── my_plugin/
    ├── plugin.toml   # metadata
    └── plugin.py     # code: must define a class named Plugin
```

`plugin.toml`:

```toml
name = "My Game: voice audio"   # required
version = "0.1"
author = "your name"
kind = "game"                   # required: "game" or "provider"
appids = ["12345"]              # Steam appids, as strings (required for "game")
```

Find a game's appid in its Steam store URL: `store.steampowered.com/app/<appid>/`.

#### Game plugins

A game plugin knows where a game keeps its audio and how to read and replace it. When a game plugin is installed, the game shows a green badge regardless of the scan result.

```python
from pathlib import Path

from revoz.core.plugins import GamePlugin


class Plugin(GamePlugin):
    def list_audio(self, game_path: Path) -> list[str]:
        """Return an id for each audio file in the game (for example, its internal path)."""

    def extract(self, game_path: Path, audio_id: str, out_dir: Path) -> Path:
        """Write that audio to out_dir as WAV and return the path."""

    def replace(self, game_path: Path, files: dict[str, Path]) -> None:
        """Replace game audio with new WAV files {audio_id: new_wav}.
        Always back up the original first."""
```

Rules for game plugins:

- **Always back up** original files before replacing them, so the user can restore the game.
- **Never write outside the game folder.** Validate every `audio_id` (see `_inside()` in the example).
- **Never upload** game audio, assets or decryption keys to the Revoz repository. Plugins that need keys or decryption are distributed by their authors, not by Revoz.

A complete working example lives in [`examples/plugins/half_life_wav`](examples/plugins/half_life_wav). Add it with **Plugins → Add folder** to try it.

#### Provider plugins (AI services)

A provider plugin adds a new translation or dubbing service to the AI selector.

```toml
name = "My AI Service"
version = "0.1"
author = "your name"
kind = "provider"
```

```python
from revoz.core.providers import Provider, get_json


class Plugin(Provider):
    name = "My AI Service"

    def languages(self, key: str) -> set[str] | None:
        """Validate the key and return the supported target language codes
        in lowercase (e.g. {"es", "en", "pt-br"}), or None if the service
        translates any common language (like large language models)."""
        data = get_json("https://api.example.com/v1/languages", {"Authorization": f"Bearer {key}"})
        return {lang["code"].lower() for lang in data}
```

Rules for provider plugins:

- Send the API key **only in HTTP headers**, never in the URL.
- Use HTTPS only (`get_json` enforces it and handles errors for you).
- Never log, print or save the key.

### Project structure

```
src/revoz/
├── core/            # logic, no UI code
│   ├── steam.py     # Steam library detection
│   ├── scan.py      # audio signature scanner
│   ├── plugins.py   # plugin loading and trust
│   ├── providers.py # AI services
│   └── keys.py      # API keys (OS secure store)
└── ui/              # PySide6 interface
```

### Contributing

Contributions are welcome: game plugins, AI services, format detection and translations of the interface. Please:

- Never commit game files, audio, keys or third-party translations. Test fixtures must be synthetic.
- Keep logic in `core/` and UI code in `ui/`.
- Add a test for any new logic.

### License

To be decided (MIT planned).

---

## Español

Revoz es una herramienta de escritorio open source para traducir las **voces** de los videojuegos con el servicio de IA que elijas.

Analiza tu biblioteca de Steam, te dice qué juegos tienen audio que se puede traducir y te deja usar tu propia API key de cualquier servicio de traducción o doblaje. La comunidad la amplía con **plugins**: un plugin por juego (o por motor) que sabe extraer y reemplazar el audio de ese juego, y plugins que suman nuevos servicios de IA.

> **Estado: desarrollo temprano.** Ya funcionan el análisis de la biblioteca, el sistema de plugins, el guardado de API keys y la comprobación de idiomas. El proceso de traducción en sí (extraer → transcribir → traducir → sintetizar → reemplazar) todavía no está implementado.

### Qué es Revoz (y qué no)

- Revoz es **solo una herramienta**. No incluye, aloja, distribuye ni enlaza archivos de juegos, audios, claves de cifrado ni traducciones.
- Usás **tu propia** API key. Revoz no incluye ni paga ningún servicio de IA.
- Lo que generes con Revoz es tu responsabilidad. Respetá los términos de los juegos que tenés y los derechos de sus actores de voz.

### Funciones

- Detecta los juegos instalados en Steam, con sus carátulas.
- Analiza automáticamente los archivos de cada juego y muestra un ícono en su carátula:

| Ícono | Significado |
|---|---|
| ✓ verde | Audio legible (WAV, Ogg, MP3, FLAC, FMOD, Wwise): fácil de traducir. O hay un plugin que soporta el juego. |
| ! amarillo | Audio empaquetado en contenedores sin cifrar (bundles de Unity, `.pak` de Unreal, `.vpk` de Valve, `.rpf` de RAGE). Necesita un plugin que lo extraiga. |
| ✗ rojo | Audio cifrado (contenedores RAGE/Unreal cifrados, o archivos de audio que no coinciden con su formato), o no se encontró audio. |

- El análisis es **de solo lectura**: Revoz lee solo las cabeceras de los archivos y nunca modifica tus juegos. Los resultados se guardan y se actualizan cuando Steam actualiza un juego.
- Las API keys se guardan en el almacén seguro del sistema (Administrador de credenciales de Windows), nunca en texto plano.
- Selector de idioma destino con un botón **Comprobar idioma** que le pregunta al servicio elegido si soporta ese idioma y valida tu clave.
- Servicios incluidos: OpenAI, Anthropic, DeepL, ElevenLabs, Google Translate. Se pueden sumar más con plugins.

### Requisitos

- Windows 10/11 (Linux puede funcionar, pero no está probado)
- Python 3.11 o superior
- Steam

### Ejecutar desde el código fuente

```bash
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\revoz
```

Correr los tests:

```bash
.venv\Scripts\python -m pytest
```

### Generar el .exe

Hacé doble clic en `build.bat` (o correlo desde una terminal). Genera `dist\Revoz.exe`, un solo archivo que funciona sin tener Python instalado. La carpeta `plugins` se crea al lado del `.exe` la primera vez que se abre.

### Plugins

#### Dónde van los plugins

Los plugins van en la carpeta `plugins` **junto a Revoz** (al lado de `Revoz.exe`, o en la raíz del proyecto si lo corrés desde el código). Cada plugin es una carpeta adentro.

Lo más fácil es manejarlos desde la pestaña **Plugins**:

- **Agregar desde .zip** o **Agregar carpeta**: instala un plugin que descargaste. No se ejecuta nada hasta que lo aprobás.
- **Quitar**: manda la carpeta del plugin a la papelera de reciclaje.
- **Recargar**: detecta plugins que copiaste a mano.
- La columna **Estado** muestra si cada plugin está activo, no aprobado o si falló al cargar (con el motivo).

La lista de plugins aprobados se guarda en tu perfil de usuario (`%APPDATA%\Revoz`), no en la carpeta de Revoz. Así, una copia de Revoz descargada no puede traer plugins ya aprobados.

#### Seguridad

Los plugins son código Python que se ejecuta en tu PC. Revoz **no** ejecuta un plugin hasta que lo aprobás. Cuando encuentra uno nuevo, muestra su nombre, autor y carpeta, y pregunta si querés activarlo. Si cambia cualquier archivo de un plugin ya aprobado, vuelve a preguntar. Instalá solo plugins de gente en la que confíes.

#### Estructura de un plugin

```
plugins/
└── mi_plugin/
    ├── plugin.toml   # metadatos
    └── plugin.py     # código: tiene que definir una clase llamada Plugin
```

`plugin.toml`:

```toml
name = "Mi Juego: audio de voces"   # obligatorio
version = "0.1"
author = "tu nombre"
kind = "game"                       # obligatorio: "game" o "provider"
appids = ["12345"]                  # appids de Steam, como texto (obligatorio para "game")
```

El appid de un juego está en la URL de su página de Steam: `store.steampowered.com/app/<appid>/`.

#### Plugins de juego

Un plugin de juego sabe dónde guarda el audio un juego y cómo leerlo y reemplazarlo. Cuando hay un plugin de juego instalado, el juego muestra el ícono verde sin importar el resultado del análisis.

```python
from pathlib import Path

from revoz.core.plugins import GamePlugin


class Plugin(GamePlugin):
    def list_audio(self, game_path: Path) -> list[str]:
        """Devuelve un id por cada audio del juego (por ejemplo, su ruta interna)."""

    def extract(self, game_path: Path, audio_id: str, out_dir: Path) -> Path:
        """Escribe ese audio en out_dir como WAV y devuelve la ruta."""

    def replace(self, game_path: Path, files: dict[str, Path]) -> None:
        """Reemplaza audios del juego con WAV nuevos {audio_id: wav_nuevo}.
        Siempre hacé backup del original antes."""
```

Reglas para los plugins de juego:

- **Siempre hacé backup** de los archivos originales antes de reemplazarlos, para que el usuario pueda restaurar el juego.
- **Nunca escribas fuera de la carpeta del juego.** Validá cada `audio_id` (mirá `_inside()` en el ejemplo).
- **Nunca subas** audios, assets ni claves de descifrado de juegos al repositorio de Revoz. Los plugins que necesitan claves o descifrado los distribuyen sus autores, no Revoz.

Hay un ejemplo completo y funcional en [`examples/plugins/half_life_wav`](examples/plugins/half_life_wav). Agregalo con **Plugins → Agregar carpeta** para probarlo.

#### Plugins de proveedor (servicios de IA)

Un plugin de proveedor suma un nuevo servicio de traducción o doblaje al selector de IA.

```toml
name = "Mi Servicio de IA"
version = "0.1"
author = "tu nombre"
kind = "provider"
```

```python
from revoz.core.providers import Provider, get_json


class Plugin(Provider):
    name = "Mi Servicio de IA"

    def languages(self, key: str) -> set[str] | None:
        """Valida la clave y devuelve los códigos de idioma destino soportados
        en minúsculas (ej. {"es", "en", "pt-br"}), o None si el servicio
        traduce cualquier idioma común (como los modelos de lenguaje)."""
        data = get_json("https://api.example.com/v1/languages", {"Authorization": f"Bearer {key}"})
        return {lang["code"].lower() for lang in data}
```

Reglas para los plugins de proveedor:

- Mandá la API key **solo en las cabeceras HTTP**, nunca en la URL.
- Usá solo HTTPS (`get_json` lo exige y maneja los errores por vos).
- Nunca muestres, registres ni guardes la clave.

### Estructura del proyecto

```
src/revoz/
├── core/            # lógica, sin código de interfaz
│   ├── steam.py     # detección de la biblioteca de Steam
│   ├── scan.py      # escáner de firmas de audio
│   ├── plugins.py   # carga y confianza de plugins
│   ├── providers.py # servicios de IA
│   └── keys.py      # API keys (almacén seguro del sistema)
└── ui/              # interfaz PySide6
```

### Contribuir

Las contribuciones son bienvenidas: plugins de juegos, servicios de IA, detección de formatos y traducciones de la interfaz. Por favor:

- Nunca subas archivos de juegos, audios, claves ni traducciones de terceros. Los datos de prueba tienen que ser sintéticos.
- Mantené la lógica en `core/` y la interfaz en `ui/`.
- Agregá un test para cualquier lógica nueva.

### Licencia

A definir (se planea MIT).
