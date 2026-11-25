# Changelog

**[English](#english) · [Español](#español)**

---

## English

### 0.3.0 - Translate, voice and install

**Translate**
- Local, free transcription of the game's voices (Whisper) and translation with the AI you choose: OpenAI, Anthropic,
  Google Gemini, Mistral, DeepSeek, xAI, Groq, Cohere, OpenRouter, DeepL, Google Translate, or **Ollama** (free, on your PC).
- Glossary, per-character context, cache (never pay twice for the same line), spending limit, test mode, pause and resume.
- Clear error messages from each AI service, and a cheap, current model picked by default.
- Tracks screen: play each line, choose which ones to translate, translate only part of a clip (From / To) and a player
  with the waveform.

**Voice**
- Translated voice with Windows voices (free), one voice per character, automatic default voice, "Spread voices" button
  and pronunciation dictionary.
- The new voice is fitted to the original's length without changing its pitch, in the same format as the game's audio.

**Install into the game**
- **Install into the game** and **Restore original** buttons: every original is backed up outside the game folder first,
  the new audio is written in the game's own format (WAV 8/16-bit, OGG, MP3, FLAC, Opus), and if anything fails halfway
  the changes are rolled back. Revoz notices when a store update replaces those files.
- The game page tells you when the game already has **official voices** in your language on Steam.

**Game formats**
- New **Dialogue** box on each game page: audio format, folder, and whether Revoz reads it, can extract it, needs a plugin
  or it's encrypted. Detects Wwise, FMOD, CRI, Unity, Godot, Unreal, RAGE, Valve and Ubisoft formats.
- Built-in reader for **Valve `.vpk`** packages: an **Extract** button takes the voices out to `<game>/Revoz`.
  Source 1 games install the translation into `custom/` without touching the original package.

**Notes**
- Revoz never decrypts protected files and doesn't include or download game files.
- If a game has an online mode with anti-cheat, use translations for offline play only.

### 0.2.0 - Interface and foundation

- Game library (Steam, Epic, GOG, manual games and folders) with automatic audio analysis.
- Plugins for games, AI services, themes and interface languages; API keys in the Windows secure store.
- Translation screen, local profile, themes, and the interface in Spanish, English, Portuguese and Chinese.

---

## Español

### 0.3.0 - Traducir, poner voz e instalar

**Traducir**
- Transcripción local y gratis de las voces del juego (Whisper) y traducción con la IA que elijas: OpenAI, Anthropic,
  Google Gemini, Mistral, DeepSeek, xAI, Groq, Cohere, OpenRouter, DeepL, Google Translate, u **Ollama** (gratis, en tu PC).
- Glosario, contexto por personaje, caché (nunca pagar dos veces la misma línea), límite de gasto, modo prueba, pausar y seguir.
- Mensajes de error claros de cada servicio de IA, y un modelo barato y actual elegido por defecto.
- Pantalla de pistas: escuchar cada línea, elegir cuáles traducir, traducir solo una parte (Desde / Hasta) y un reproductor
  con la forma de onda.

**Voz**
- Voz traducida con las voces de Windows (gratis), una voz por personaje, voz por defecto automática, botón "Repartir voces"
  y diccionario de pronunciación.
- La voz nueva se ajusta al largo del original sin cambiar el tono, en el mismo formato que el audio del juego.

**Instalar en el juego**
- Botones **Instalar en el juego** y **Restaurar original**: primero se guarda una copia de cada original fuera de la
  carpeta del juego, el audio nuevo se escribe en el formato del juego (WAV 8/16 bits, OGG, MP3, FLAC, Opus), y si algo
  falla a mitad de camino se deshace. Revoz se da cuenta si una actualización de la tienda cambia esos archivos.
- La página del juego avisa si el juego ya tiene **voces oficiales** en tu idioma en Steam.

**Formatos de juegos**
- Nuevo cuadro **Diálogos** en la página de cada juego: formato del audio, carpeta, y si Revoz lo lee, lo puede extraer,
  necesita un plugin o está cifrado. Detecta formatos de Wwise, FMOD, CRI, Unity, Godot, Unreal, RAGE, Valve y Ubisoft.
- Lector incluido para paquetes **Valve `.vpk`**: un botón **Extraer** saca las voces a `<juego>/Revoz`.
  En los juegos Source 1 la traducción se instala en `custom/` sin tocar el paquete original.

**Notas**
- Revoz nunca descifra archivos protegidos y no incluye ni descarga archivos de juegos.
- Si un juego tiene modo online con anticheat, usá las traducciones solo para jugar offline.

### 0.2.0 - Interfaz y base

- Biblioteca de juegos (Steam, Epic, GOG, juegos manuales y carpetas) con análisis automático del audio.
- Plugins de juegos, servicios de IA, temas e idiomas de la interfaz; API keys en el almacén seguro de Windows.
- Pantalla de traducción, perfil local, temas, e interfaz en español, inglés, portugués y chino.
