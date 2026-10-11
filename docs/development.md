# Desarrollo

## Requisitos

- [uv](https://docs.astral.sh/uv/) (instala Python 3.12 por ti).
- Node.js 22 o superior, para compilar la GUI.
- Nada más para los modelos: el asistente de primer uso descarga el motor llama.cpp y los modelos del catálogo. Si lo prefieres, puedes usar tu propio servidor compatible con OpenAI (Ollama, LM Studio, llama.cpp, vLLM o MLX).

## Arrancar

```bash
uv run game-pal
```

uv instala Python y las dependencias. Si la GUI no está compilada o sus fuentes han cambiado, game-pal la compila antes de arrancar (instala sus dependencias con `npm ci` cuando hace falta). Luego abre la GUI en el navegador con un token de sesión en la dirección; si cierras la pestaña, la dirección completa sigue en la terminal. Si vuelves a lanzar game-pal mientras está en marcha, no arranca otro: le pide al que ya está que abra su GUI.

Al arrancar avisa en la terminal de lo que impide dar consejos (por ejemplo, que no hay modelo). Para ver todas las comprobaciones con su solución:

```bash
uv run game-pal doctor          # --json para usarlo desde scripts
```

La GUI obtiene las mismas comprobaciones de `GET /api/setup`.

### Asistente de primer uso

Mientras `setup_completed` sea falso, la GUI abre un asistente en cuatro pasos:

1. **Juego**: detección automática, perfil, ventana e idiomas.
2. **Modelo de IA**, con tres opciones y un botón para probarlo:
   - *Instálalo con game-pal*: detecta el equipo, propone un nivel (0-3) y descarga el motor llama.cpp y los modelos verificados del catálogo (`POST /api/setup/tier` y `/api/engine/install`). Ocupan entre 2 y 21 GB según el nivel y quedan en la carpeta de datos, en `engine/`.
   - *Ya tengo uno*: los servidores de Ollama, LM Studio, llama.cpp o MLX que encuentra en este equipo.
   - *En la nube*: clave y modelo de OpenAI, Claude u OpenRouter.
3. **Comprobación**: `/api/setup` con la solución de cada problema y un botón que abre el panel de permisos del sistema.
4. **Voz**: hablarle, que conteste en voz alta, y pruebas de micrófono y sonido.

Terminarlo o saltarlo lo marca como hecho; se puede volver a abrir desde Avanzado. `POST /api/setup/settings/{pane}` abre solo paneles de una lista fija (`system.SETTINGS_PANES`). La configuración antigua con `setup_done` se migra sola a `setup_completed`.

### La GUI

- El menú tiene arriba lo que casi todo el mundo usa (Inicio, Juego, Modelos, Voz y respuestas) y en «Más» lo demás (Conocimiento, Memoria, Actividad, Avanzado).
- Los cambios se guardan solos: `useConfig().setConfig` guarda 700 ms después del último cambio y, al salir de la página, guarda lo pendiente. `save` guarda al momento, para los botones que prueban algo. El daemon solo reinicia la captura si cambia algo que la afecta.
- Lo que casi nadie necesita va dentro de «Opciones avanzadas» (`<Advanced>`).
- Inicio muestra «Lo que falta», con las comprobaciones de `/api/setup` y un botón para ir a arreglarlo.

### Detección automática

game-pal rellena lo que falte sin tocar lo que el jugador eligió a mano:

- **Modelo local** (solo con un servidor propio, no con el motor de game-pal): si no hay ninguno elegido, prueba el servidor configurado y los puertos de Ollama (11434), LM Studio (1234), llama.cpp (8080) y MLX (8081), y elige el modelo de conversación más capaz que cabe en la mitad de la RAM, sin pasar de 14B. Lo intenta al arrancar y, mientras falte, cada 30 segundos.
- **Juego**: cada 5 segundos, si el juego del perfil activo no está abierto y sí el de otro perfil, cambia a ese perfil. Se desactiva al elegir un perfil o una ventana a mano (`auto_detect_game`).
- **Idiomas**: sin configuración guardada, el del juego y el de las respuestas son el del sistema.

`POST /api/setup/autodetect` lo lanza al momento y `GET /api/providers/local/discover` lista los servidores en marcha con su modelo recomendado.

En macOS, la primera vez el sistema pedirá permisos para la app desde la que lances game-pal (Terminal, iTerm, VS Code…). Concédelos y vuelve a arrancar:

- **Grabación de pantalla**, para ver la ventana del juego.
- **Micrófono**, si activas la voz.
- **Monitorización de entrada** o **Accesibilidad**, para los atajos de teclado globales.

En Linux, la voz necesita PortAudio (`libportaudio2`) y los atajos globales solo funcionan en X11.

La primera vez que actives la voz se descargan los modelos: Silero VAD (2 MB), Whisper (75 MB a 1,5 GB según el modelo) y la voz de Piper del idioma (unos 60 MB). Quedan en la caché de game-pal.

En Windows ARM64 no se instala RapidOCR (OpenCV no publica ruedas para esa arquitectura) y el OCR por defecto es **Windows OCR**, que viene con el sistema. Lee un solo idioma a la vez y necesita su paquete de idioma instalado (Configuración › Hora e idioma); si falta el del juego, usa los idiomas de tu cuenta. No da confianza por línea, así que el umbral de confianza no le afecta.

## Comandos útiles

```bash
uv run game-pal doctor                        # qué funciona, qué falta y cómo arreglarlo
uv run game-pal windows                       # ventanas que se pueden capturar
uv run game-pal capture --title "Stardew"      # guarda una captura de esa ventana
uv run game-pal ocr captura.png --lang es      # lee el texto de una imagen
uv run game-pal profiles                       # lista y valida los perfiles
uv run game-pal new-profile mi-juego --name "Mi Juego"   # esqueleto de un perfil nuevo
uv run game-pal schema --out profile.schema.json
```

## Calidad

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest
```

Los tests usan imágenes sintéticas y un backend de captura falso: no capturan tu pantalla, no abren el micrófono ni llaman a ningún modelo. Los marcados como `ocr` cargan RapidOCR real y se omiten donde no está instalado; los marcados como `windows_ocr` solo se ejecutan en Windows.

Las pruebas de voz con modelos reales generan habla con Piper y la pasan por el detector y Whisper, sin micrófono. Descargan modelos, así que van aparte:

```bash
uv run pytest -m voice
```

## GUI en modo desarrollo

```bash
uv run game-pal --dev
```

Arranca el daemon y Vite juntos y abre la GUI de Vite con el token ya puesto, con recarga en caliente. Vite redirige `/api` (también el WebSocket) al daemon, sea cual sea su puerto. Al cerrar game-pal se cierra Vite.

## Empaquetado

`uv build` compila la GUI y la mete en el wheel (`scripts/hatch_build.py`), así quien lo instala no necesita Node.js. Las instalaciones editables de `uv sync` no la compilan: lo hace game-pal al arrancar.

## Empaquetar la app

```bash
uv sync --group build
uv run python scripts/build.py                  # compila la GUI si hace falta y empaqueta
uv run python scripts/smoke.py dist/game-pal.app/Contents/MacOS/game-pal   # prueba de humo
```

El resultado queda en `dist/release/`: un DMG en macOS, un zip portable y, si tienes Inno Setup, un instalador en Windows, y un tar.gz y, si tienes appimagetool, un AppImage en Linux. Cada sistema se empaqueta en su propio sistema.

Al subir una etiqueta `v*`, el flujo **Release** empaqueta en los cuatro sistemas, prueba cada app, añade la atestación de procedencia y crea un borrador de versión con las sumas SHA-256. Un pull request que toque el empaquetado compila y prueba sin publicar.

## Estructura

```
gamepal/
  bus.py, events.py      bus de eventos en proceso y eventos tipados
  config.py, paths.py    configuración validada y rutas por sistema
  capture/               un backend por sistema y la elección de ventana
  perception/            hash perceptual, OCR enchufable y bucle de observación
  profiles/              esquema y carga de perfiles de juego
  providers/             interfaz de modelos, proveedor local, OpenAI, OpenRouter, Claude y enrutado por tarea
  keystore.py            claves de API en el almacén de credenciales del sistema
  hardware.py            detección del equipo y nivel recomendado
  runtime/               catálogo de modelos, descargas verificadas y motor llama.cpp gestionado
  tray.py                icono en la bandeja de la app instalada
  activity.py            registro local de peticiones, tokens y coste
  voice/                 micrófono, detector de voz, Whisper, Piper, frases clave, atajos y mando
  overlay/               proceso del overlay: lógica común y ventana nativa por sistema
  evidence/              decodificadores LZ4 y LuaBins, registro de hechos por rangos y vigilancia del guardado
  plugins.py             carga de plugins de perfil y confianza por hash
  memory.py              memoria opcional entre sesiones
  knowledge/             descarga segura, extracción, troceado, embeddings, índice SQLite y búsqueda híbrida
  assistant.py           preguntas, enrutado rápido/razonado, conocimiento con citas y avisos proactivos
  gui_server/            API REST + WebSocket en loopback con token
  daemon.py, cli.py      orquestación y línea de comandos
  checks.py              comprobaciones de puesta en marcha (game-pal doctor y /api/setup)
  autodetect.py          elección automática de servidor y modelo local y del perfil del juego abierto
  system.py              idioma del sistema y memoria del equipo
  ui_build.py            compilación de la GUI al arrancar desde el repositorio
ui/                      GUI en React + TypeScript (Vite)
models/                  catálogo de modelos y del motor, con revisiones y hashes fijados
packaging/, scripts/     empaquetado con PyInstaller, instaladores y prueba de humo
profiles/                perfiles incluidos
tests/
```

## Seguridad

- El servidor solo escucha en `127.0.0.1`, comprueba la cabecera `Host` y exige un token aleatorio por arranque.
- El token viaja en el fragmento de la URL, que el navegador nunca envía al servidor, y no se escribe en el registro.
- Los proveedores locales solo aceptan direcciones de loopback, sin proxies ni redirecciones.
- El motor local escucha solo en 127.0.0.1 con una clave aleatoria por arranque, sin red ni interfaz web; sus ficheros se verifican por SHA-256 y muere con game-pal aunque este se cierre de golpe.
- Las claves de la nube viven en el almacén del sistema (Llavero, Credential Manager o Secret Service); la API solo dice si existen y nunca las devuelve. Las capturas no salen a la nube salvo que el jugador lo active.
- Los perfiles se validan con un esquema estricto y sus rutas no pueden salir de su carpeta.
- Los guardados se leen sin escribirlos nunca, con límites de tamaño y profundidad; los plugins de perfiles de terceros solo se cargan tras confiar en ellos por su hash.
- Las fuentes de conocimiento solo se descargan de direcciones públicas, también tras redirecciones, respetando `robots.txt`; los ficheros subidos se guardan con un nombre saneado dentro de la carpeta del perfil.
