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
- **Cualquier modelo**: Ollama, LM Studio, llama.cpp o cualquier endpoint compatible, además de OpenAI, Anthropic y OpenRouter.
- **Cualquier juego**: perfiles declarativos sin coordenadas fijas; conocimiento cargado por URL y actualizable con un botón.
- **Multiidioma**: idioma del juego, del jugador y de las respuestas configurables por separado.
- **Tres formas de pedir consejo**: palabra clave, push-to-talk (teclado o mando) y modo proactivo.
- **Tres formas de recibirlo**: overlay, texto y voz.
- **Fuentes de verdad**: lo que se ve en pantalla y, cuando el perfil lo soporta, el fichero de guardado del juego.
- **Memoria opcional** por sesión, exportable y borrable.
- **GUI** para configurarlo y controlarlo todo sin tocar ficheros.
- **Seguridad por defecto**: claves en el almacén de credenciales del sistema, solo loopback, sin telemetría.

## Estado

En desarrollo. Ya funciona: captura de la ventana del juego, lectura del texto, reconocimiento de pantallas por perfil (con Hades II y Stardew Valley incluidos), preguntas por voz con frase clave o pulsando una tecla o un botón del mando, respuestas habladas y sobre el juego, avisos por su cuenta en los momentos clave, conocimiento sacado de wikis y documentos con fuentes citadas, lectura del guardado de Hades II y Stardew Valley para saber cómo vas, memoria opcional entre sesiones, OpenAI, Claude y OpenRouter opcionales por tarea con un panel de actividad, y una GUI para controlarlo todo. Todavía no hay versión instalable.

La especificación completa está en [docs/spec.md](docs/spec.md).

## Probarlo

```bash
uv sync
npm --prefix ui install && npm --prefix ui run build
uv run game-pal
```

Más detalles en [docs/development.md](docs/development.md). Para enseñarle un juego nuevo, mira [docs/profiles.md](docs/profiles.md).

## Contribuir

Las ideas, perfiles de juego y correcciones son bienvenidos. Abre un issue para proponer algo o un pull request con tu perfil.

## Licencia

[MIT](LICENSE)
