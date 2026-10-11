# Desarrollo

## Requisitos

- [uv](https://docs.astral.sh/uv/) (instala Python 3.12 por ti).
- Node.js 22 o superior, solo para compilar la GUI.
- Un servidor de modelos local compatible con OpenAI si quieres hacer preguntas: Ollama, LM Studio, llama.cpp, vLLM o MLX.

## Arrancar

```bash
uv sync
npm --prefix ui install
npm --prefix ui run build
uv run game-pal
```

game-pal abre la GUI en el navegador con un token de sesión en la dirección. Si cierras la pestaña, la dirección completa aparece en la terminal.

En macOS, la primera vez el sistema pedirá permiso de **Grabación de pantalla** para la app desde la que lances game-pal (Terminal, iTerm, VS Code…). Concédelo y vuelve a arrancar.

## Comandos útiles

```bash
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

Los tests usan imágenes sintéticas y un backend de captura falso: no capturan tu pantalla ni llaman a ningún modelo. Los marcados como `ocr` cargan el motor OCR real.

## GUI en modo desarrollo

```bash
uv run game-pal --no-open      # en una terminal
npm --prefix ui run dev        # en otra
```

Vite redirige `/api` al daemon. Abre la dirección de Vite añadiendo el `#token=…` que imprime el daemon.

## Estructura

```
gamepal/
  bus.py, events.py      bus de eventos en proceso y eventos tipados
  config.py, paths.py    configuración validada y rutas por sistema
  capture/               un backend por sistema y la elección de ventana
  perception/            hash perceptual, OCR enchufable y bucle de observación
  profiles/              esquema y carga de perfiles de juego
  providers/             interfaz de modelos y proveedor local OpenAI-compatible
  gui_server/            API REST + WebSocket en loopback con token
  daemon.py, cli.py      orquestación y línea de comandos
ui/                      GUI en React + TypeScript (Vite)
profiles/                perfiles incluidos
tests/
```

## Seguridad

- El servidor solo escucha en `127.0.0.1`, comprueba la cabecera `Host` y exige un token aleatorio por arranque.
- El token viaja en el fragmento de la URL, que el navegador nunca envía al servidor, y no se escribe en el registro.
- Los proveedores locales solo aceptan direcciones de loopback, sin proxies ni redirecciones.
- Los perfiles se validan con un esquema estricto y sus rutas no pueden salir de su carpeta.
