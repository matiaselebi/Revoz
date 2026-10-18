# Contexto del proyecto: Traductor de voces de videojuegos con IA

> Documento para arrancar el proyecto con Claude Code. Copialo a la raíz del repo como `CLAUDE.md` o `docs/CONTEXTO.md`.

## 1. Qué es

Herramienta de escritorio **open source** que:

1. Detecta los juegos instalados (Steam primero, después otras tiendas o carpeta manual).
2. Extrae los audios del juego.
3. Transcribe cada línea (Whisper).
4. Traduce con el modelo de IA que elija el usuario (local con Ollama o API paga con su propia clave).
5. Genera la versión traducida: subtítulos (.srt), voz (TTS) o reemplazo de archivos, según la fase.
6. Gestiona traducciones y mods de la comunidad que el usuario ya haya descargado (por ejemplo, un doblaje al español que consiguió por fuera).

**Visión:** que la comunidad aporte adaptadores para más motores y juegos, y que la herramienta crezca como proyecto abierto.

**Nombre provisional:** por definir. No usar nombres de juegos ni marcas registradas.

## 2. Decisiones ya tomadas

- **Lenguaje del núcleo:** Python 3.11+.
- **Interfaz del MVP:** PySide6 (Qt). Dejar FastAPI + frontend web o Tauri como opción para más adelante, sin acoplar el núcleo a la UI.
- **Modo de trabajo principal: offline (pre-traducción).** Se traduce una vez, se guarda en caché y se reutiliza.
- **Modo tiempo real: opcional, seleccionable por el usuario**, pero no entra en el MVP. Primero subtítulos en vivo, después voz.
- **Local-first:** todo corre en la PC del usuario. Las APIs de pago son opcionales y el usuario pone su propia clave.
- **Claves de API:** guardadas con `keyring` (Windows Credential Manager). Nunca en el repo, nunca en texto plano, nunca en logs.
- **Caché obligatoria:** clave por hash del audio original + modelo + versión del glosario. Evita pagar dos veces.
- **Traducciones existentes:** la herramienta **importa y gestiona** archivos que el usuario ya obtuvo. No aloja, no distribuye ni enlaza assets, audios ni traducciones de juegos comerciales.
- **Publicación:** repositorio público en GitHub, con README claro, licencia y guía de contribución.
- **Licencia:** pendiente de confirmar. Candidata: MIT. Revisar compatibilidad con las dependencias (por ejemplo, vgmstream).

## 3. Qué NO hace el repo (reglas duras)

- No se commitean audios, assets, `.bnk`, `.wem`, `.rpf`, `.assets` ni archivos de juegos. Agregar a `.gitignore` y a los tests de pre-commit.
- No se commitean claves de API, `.env` ni archivos de configuración del usuario.
- No se incluyen traducciones comunitarias ni mods de terceros en el repo.
- No se modifican juegos con anti-cheat o multijugador sin advertencia explícita en la UI.

## 4. Alcance por fase

### Fase 1: MVP (prioridad)
- Un motor: **Unity** (UnityPy).
- Un juego de prueba propio o libre, nunca comercial en el repo.
- Detección de juegos de Steam (`libraryfolders.vdf`, `appmanifest_*.acf`).
- Extracción de audio a WAV con índice (archivo, personaje, línea si está disponible).
- Transcripción con **faster-whisper**.
- Traducción con un proveedor local (**Ollama**) y uno de pago (**OpenAI** o **Anthropic**), detrás de una interfaz común. Soporte de glosario y contexto por personaje.
- Salida en **.srt** traducido y en **CSV/JSON** de revisión.
- Caché por hash.
- UI mínima: elegir juego, elegir proveedor de IA, ver progreso, editar líneas antes de exportar.

### Fase 2: Voz y reemplazo
- TTS: **Piper** (local) o **ElevenLabs** (API), como plugins.
- Reemplazo de audio en el juego con backup y restauración.
- Sistema de traducciones/mods de la comunidad: importar, verificar compatibilidad (checksum o build), activar, desactivar y revertir sin borrar.

### Fase 3: Tiempo real (opcional por usuario)
- Captura de audio por loopback, transcripción y traducción en vivo.
- Primero subtítulos superpuestos. Voz en vivo después.
- Selector en la UI: **Pre-traducir** o **En vivo**. Mismo pipeline, mismo glosario, misma caché.
- Límite de gasto mensual o por sesión para las APIs pagas.

### Fuera del MVP
- Motores propietarios, clonación de voces, otras tiendas (Epic, GOG, Xbox), macOS y Linux.

## 5. Arquitectura

```
proyecto/
├── pyproject.toml
├── README.md
├── LICENSE
├── CONTRIBUTING.md
├── SECURITY.md
├── .gitignore              # excluye assets, audios, claves, .env, caché
├── src/
│   └── voxtrans/           # nombre de paquete provisional
│       ├── core/           # pipeline, modelos de datos, caché, config
│       ├── library/        # detección de juegos (Steam, luego otras tiendas)
│       ├── games/          # un adaptador por motor (unity/, wwise/, ...)
│       ├── extract/        # extracción de audio y conversión a WAV
│       ├── stt/            # transcripción (faster-whisper)
│       ├── translate/      # proveedores de traducción (ollama, openai, anthropic)
│       ├── tts/            # plugins de voz (piper, elevenlabs) — fase 2
│       ├── patches/        # importar, verificar, activar y revertir traducciones — fase 2
│       ├── live/           # modo tiempo real — fase 3
│       ├── secrets/        # acceso a claves vía keyring
│       └── ui/             # interfaz (PySide6)
├── tests/
│   ├── fixtures/           # solo datos sintéticos o de juegos libres, nunca de juegos comerciales
│   └── ...
└── docs/
```

**Interfaces clave (definir primero):**
- `GameAdapter`: `detect()`, `list_audio()`, `extract(audio_id) -> WAV`, `can_replace()`.
- `Translator`: `translate(lines, context, glossary) -> lines`.
- `Transcriber`: `transcribe(wav) -> text`.
- `TTSProvider` (fase 2): `synthesize(text, voice) -> WAV`.
- Modelo de datos `Line`: id, archivo fuente, personaje, texto original, texto traducido, hash, estado de revisión.

## 6. Primera tarea para Claude Code

En este orden:

1. Crear el esqueleto del repo con la estructura de la sección 5, `pyproject.toml`, `.gitignore`, licencia (MIT, si se confirma) y README con el objetivo y la advertencia legal.
2. Configurar linting (ruff), tipos (mypy o pyright) y pytest. Agregar un workflow de GitHub Actions que corra tests en Windows y Linux.
3. Implementar `library/steam.py`: parsear `libraryfolders.vdf` y `appmanifest_*.acf` y devolver nombre, appid y ruta de instalación. Tests con fixtures sintéticos.
4. Definir las interfaces `GameAdapter`, `Translator`, `Transcriber` y el modelo `Line`, con tests de contrato.
5. Implementar el caché por hash con tests.
6. Recién después: adaptador Unity y pipeline offline.

Pedirle a Claude Code que **se detenga y pregunte** antes de: elegir la licencia final, agregar una dependencia con licencia incompatible, o tocar cualquier archivo binario de juego.

## 7. Dependencias y referencias

- **Extracción:** UnityPy, vgmstream (Wwise/FMOD: `.wem`, `.bnk`, `.fsb`).
- **Transcripción:** faster-whisper.
- **Traducción:** Ollama (local), SDK de OpenAI o Anthropic (API).
- **TTS:** Piper, Coqui XTTS, ElevenLabs.
- **Claves:** keyring.
- **UI:** PySide6.
- **Empaquetado:** PyInstaller o Nuitka, Inno Setup para instalador, GitHub Actions para releases.
- **Referencias de proyectos similares:** XUnity.AutoTranslator (texto en Unity, buena referencia de distribución de mods), openai-game-translator (PyPI, audio con OpenAI).

## 8. Preguntas abiertas (resolver antes o durante la fase 1)

- [ ] Nombre definitivo del proyecto y del paquete.
- [ ] Licencia: MIT o GPL-3.
- [ ] Proveedores de IA del MVP: ¿solo Ollama + una API, o más?
- [ ] Sistema operativo objetivo inicial: Windows (recomendado).
- [ ] Juego de prueba concreto (propio o libre con motor Unity).
- [ ] Idioma de la UI y de la documentación: español primero, inglés después.
- [ ] Dedicación semanal estimada.

## 9. Criterios de calidad

- Todo lo que toque la red o la API tiene modo simulado en tests.
- Ningún test depende de un juego instalado en la máquina.
- Cada fase termina con un entregable usable y documentado en el README.
- Los errores de extracción nunca dejan el juego en un estado roto: siempre backup antes de reemplazar.
