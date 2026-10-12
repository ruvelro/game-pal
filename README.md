<p align="center"><img src="assets/icons/icon-256.png" width="128" alt="game-pal"></p>

<h1 align="center">game-pal</h1>

<p align="center">Tu compañero de juego con IA: mira la pantalla, te escucha y te aconseja mientras juegas.<br>Local, privado y para cualquier juego.</p>

## Qué es

game-pal observa la ventana del juego, reconoce qué pantalla tienes delante y te da consejos cuando se lo pides por voz, por una tecla o por un botón del mando. También puede avisarte solo en los momentos clave. Responde en un overlay encima del juego, por texto o hablando.

Funciona con modelos de IA locales, así que no necesita conexión ni clave de API y nada sale de tu ordenador. Si quieres, puedes conectar OpenAI, Claude u OpenRouter para algunas tareas y combinar local y nube como prefieras.

Cada juego es un **perfil**: una carpeta con unas reglas en YAML y las webs de donde sacar el conocimiento (wikis, guías). Vienen incluidos Hades II y Stardew Valley, y crear uno nuevo no requiere programar.

## Características

- **Multiplataforma**: Windows (x64 y ARM64), macOS (Apple Silicon) y Linux.
- **Local primero**: OCR, visión, razonamiento, voz y búsqueda en tu equipo. Niveles de hardware desde un portátil modesto hasta una GPU grande.
- **Cualquier modelo, esté donde esté**: el motor local que game-pal puede instalar (llama.cpp con Gemma 4 y Qwen 3.5), tu propio servidor (Ollama, LM Studio, NVIDIA PAIR…) en este equipo o en otro de tu red, u OpenAI, Anthropic y OpenRouter. «Probar conexión» dice qué falla.
- **Cualquier juego**: perfiles declarativos sin coordenadas fijas; conocimiento cargado por URL y actualizable con un botón.
- **Multiidioma**: idioma del juego, del jugador y de las respuestas configurables por separado.
- **Tres formas de pedir consejo**: palabra clave, push-to-talk (teclado o mando) y modo proactivo.
- **Tres formas de recibirlo**: overlay, texto y voz. Y un chat encima del juego (Ctrl+Alt+C) para escribirle sin salir de la partida.
- **Fuentes de verdad**: lo que se ve en pantalla y, cuando el perfil lo soporta, el fichero de guardado del juego.
- **Memoria opcional** por sesión, exportable y borrable.
- **GUI** para configurarlo y controlarlo todo sin tocar ficheros.
- **Seguridad por defecto**: claves en el almacén de credenciales del sistema, solo loopback, sin telemetría.

## Instalar

Descarga el instalador de tu sistema desde [Releases](https://github.com/ruvelro/game-pal/releases):

| Sistema | Fichero |
| --- | --- |
| Windows x64 / ARM64 | `game-pal-…-windows-x64-setup.exe` o `…-arm64-setup.exe` (también hay zip portable) |
| macOS (Apple Silicon) | `game-pal-…-macos-arm64.dmg` |
| Linux x64 | `game-pal-…-linux-x64.AppImage` (o el tar.gz) |

Al abrirlo por primera vez, un asistente de cuatro pasos te guía: el juego (lo reconoce solo si tiene perfil), el modelo de IA (puede descargar el motor local y modelos verificados según tu equipo, usar el servidor que ya tengas o la nube), una comprobación de que todo funciona y la voz. game-pal vive después en la bandeja del sistema (en la barra de menús en macOS).

Los instaladores aún no llevan firma de código de Apple ni de Microsoft:

- **macOS**: la primera vez, haz clic derecho sobre game-pal › Abrir, o permítelo en Ajustes del Sistema › Privacidad y seguridad.
- **Windows**: si SmartScreen avisa, pulsa «Más información» › «Ejecutar de todas formas».

Para comprobar que una descarga es la publicada aquí, usa `SHA256SUMS.txt` o la atestación de procedencia:

```bash
gh attestation verify game-pal-0.1.0-macos-arm64.dmg --repo ruvelro/game-pal
```

## Estado

Versión 0.1, la primera utilizable:

- **Ver**: captura de la ventana del juego y lectura del texto, con reconocimiento de pantallas por perfil (Hades II y Stardew Valley incluidos).
- **Hablar**: preguntas por voz con frase clave o pulsando una tecla o un botón del mando, con respuestas habladas y sobre el juego, y avisos por su cuenta en los momentos clave.
- **Saber y recordar**: conocimiento sacado de wikis y documentos con fuentes citadas, lectura del guardado de Hades II y Stardew Valley, y memoria opcional entre sesiones.
- **Modelos**: motor local gestionado, OpenAI, Claude y OpenRouter opcionales por tarea, y un panel de actividad.
- **Usarlo**: un asistente de primer uso e instaladores para los tres sistemas.

La especificación completa está en [docs/spec.md](docs/spec.md) y los cambios de cada versión en [CHANGELOG.md](CHANGELOG.md).

## Desde el código

Necesitas [uv](https://docs.astral.sh/uv/) y, para compilar la GUI la primera vez, [Node.js 22](https://nodejs.org) o superior. Después:

```bash
uv run game-pal
```

Instala lo que falte, compila la GUI y la abre en el navegador. La primera vez, un asistente de cuatro pasos te guía: el juego, el modelo de IA, una comprobación de que todo funciona y la voz. Si algo no está listo, lo dice también al arrancar. Para revisarlo todo con calma:

```bash
uv run game-pal doctor
```

Para el modelo de IA no hace falta nada más: el asistente puede descargar el motor llama.cpp y un modelo verificado adecuado a tu equipo. Si ya tienes Ollama, LM Studio, llama.cpp o MLX, game-pal los encuentra solo y elige un modelo que quepa. También vale una clave de OpenAI, Claude u OpenRouter.

Tampoco hace falta elegir el juego: si tiene perfil, game-pal lo reconoce al abrirlo. Los idiomas se toman del sistema la primera vez.

Si algo falla, mira [docs/problemas.md](docs/problemas.md). Más detalles en [docs/development.md](docs/development.md). Para enseñarle un juego nuevo, mira [docs/profiles.md](docs/profiles.md) o empieza con `uv run game-pal new-profile mi-juego`.

## Contribuir

Las ideas, perfiles de juego y correcciones son bienvenidos. Abre un issue para proponer algo o un pull request con tu perfil.

## Licencia

[MIT](LICENSE)
