# Desarrollo

## Requisitos

- [uv](https://docs.astral.sh/uv/) (instala Python 3.12 por ti).
- Node.js 22 o superior, para compilar la GUI.
- Un servidor de modelos local compatible con OpenAI si quieres hacer preguntas: Ollama, LM Studio, llama.cpp, vLLM o MLX.

## Arrancar

```bash
uv run game-pal
```

uv instala Python y las dependencias. Si la GUI no está compilada o sus fuentes han cambiado, game-pal la compila antes de arrancar (instala sus dependencias con `npm ci` cuando hace falta). Luego abre la GUI en el navegador con un token de sesión en la dirección; si cierras la pestaña, la dirección completa sigue en la terminal.

Al arrancar avisa en la terminal de lo que impide dar consejos (por ejemplo, que no hay modelo). Para ver todas las comprobaciones con su solución:

```bash
uv run game-pal doctor          # --json para usarlo desde scripts
```

La GUI obtiene las mismas comprobaciones de `GET /api/setup`.

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
  ui_build.py            compilación de la GUI al arrancar desde el repositorio
ui/                      GUI en React + TypeScript (Vite)
profiles/                perfiles incluidos
tests/
```

## Seguridad

- El servidor solo escucha en `127.0.0.1`, comprueba la cabecera `Host` y exige un token aleatorio por arranque.
- El token viaja en el fragmento de la URL, que el navegador nunca envía al servidor, y no se escribe en el registro.
- Los proveedores locales solo aceptan direcciones de loopback, sin proxies ni redirecciones.
- Las claves de la nube viven en el almacén del sistema (Llavero, Credential Manager o Secret Service); la API solo dice si existen y nunca las devuelve. Las capturas no salen a la nube salvo que el jugador lo active.
- Los perfiles se validan con un esquema estricto y sus rutas no pueden salir de su carpeta.
- Los guardados se leen sin escribirlos nunca, con límites de tamaño y profundidad; los plugins de perfiles de terceros solo se cargan tras confiar en ellos por su hash.
- Las fuentes de conocimiento solo se descargan de direcciones públicas, también tras redirecciones, respetando `robots.txt`; los ficheros subidos se guardan con un nombre saneado dentro de la carpeta del perfil.
