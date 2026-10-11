# Desarrollo

## Requisitos

- [uv](https://docs.astral.sh/uv/) (instala Python 3.12 por ti).
- Node.js 22 o superior, solo para compilar la GUI.
- Nada más para los modelos: el asistente de primer uso descarga el motor llama.cpp y los modelos del catálogo. Si lo prefieres, puedes usar tu propio servidor compatible con OpenAI (Ollama, LM Studio, vLLM o MLX).

## Arrancar

```bash
uv sync
npm --prefix ui install
npm --prefix ui run build
uv run game-pal
```

game-pal abre la GUI en el navegador con un token de sesión en la dirección. Si cierras la pestaña, la dirección completa aparece en la terminal; si vuelves a lanzar game-pal mientras está en marcha, no arranca otro: le pide al que ya está que abra su GUI.

La primera vez se abre el asistente: detecta el equipo, propone un nivel, descarga el motor y los modelos verificados, prueba el micrófono y el sonido, y te deja elegir el juego. Los modelos ocupan entre 2 y 21 GB según el nivel y quedan en la carpeta de datos de game-pal, en `engine/`.

En macOS, la primera vez el sistema pedirá permisos para la app desde la que lances game-pal (Terminal, iTerm, VS Code…). Concédelos y vuelve a arrancar:

- **Grabación de pantalla**, para ver la ventana del juego.
- **Micrófono**, si activas la voz.
- **Monitorización de entrada** o **Accesibilidad**, para los atajos de teclado globales.

En Linux, la voz necesita PortAudio (`libportaudio2`) y los atajos globales solo funcionan en X11.

La primera vez que actives la voz se descargan los modelos: Silero VAD (2 MB), Whisper (75 MB a 1,5 GB según el modelo) y la voz de Piper del idioma (unos 60 MB). Quedan en la caché de game-pal.

En Windows ARM64 no se instala RapidOCR (OpenCV no publica ruedas para esa arquitectura) y el OCR por defecto es **Windows OCR**, que viene con el sistema. Lee un solo idioma a la vez y necesita su paquete de idioma instalado (Configuración › Hora e idioma); si falta el del juego, usa los idiomas de tu cuenta. No da confianza por línea, así que el umbral de confianza no le afecta.

## Comandos útiles

```bash
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
uv run game-pal --no-open      # en una terminal
npm --prefix ui run dev        # en otra
```

Vite redirige `/api` al daemon. Abre la dirección de Vite añadiendo el `#token=…` que imprime el daemon.

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
