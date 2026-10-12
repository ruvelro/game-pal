# Cambios

## Sin publicar

### Para jugar

- Chat encima del juego: Ctrl+Alt+C (o el botón de Inicio) abre el historial de preguntas y avisos con un campo para escribir, sin salir de la partida. Intro envía, Esc cierra y el teclado vuelve al juego.

### Modelos

- El servidor de modelos puede estar en otro equipo de tu red (http con su IP o nombre `.local`) o en internet por https, con clave opcional si la pide.
- «Probar conexión» en Modelos, en cada nube y en el asistente: dirección, conexión, servidor, modelo y una respuesta real, con qué hacer si algo falla. El error del servidor se muestra tal cual.
- El asistente deja escribir la dirección del servidor aunque no detecte ninguno, y no obliga a descargar nada.

### Instalar

- En macOS, game-pal pide al arrancar los permisos que necesita (Grabación de pantalla, Monitorización de entrada para los atajos y, con la voz, el micrófono). Los atajos se activan solos en cuanto se concede el permiso del teclado.
- En Windows avisa si el micrófono está bloqueado y abre su panel.

## 0.1.0

Primera versión utilizable.

### Para jugar

- Mira la ventana del juego, lee su texto y reconoce pantallas declaradas en perfiles, sin coordenadas.
- Perfiles incluidos: Hades II (con lector del guardado), Stardew Valley (con lector del guardado) y genérico.
- Preguntas por texto, por voz con frase clave o pulsando una tecla o un botón del mando.
- Respuestas en un overlay sobre el juego, por texto o habladas, y avisos por su cuenta en los momentos clave.
- Conocimiento desde wikis y documentos, con fuentes citadas.
- Memoria opcional entre sesiones, exportable y borrable.

### Modelos

- Motor local gestionado: llama.cpp en la variante del equipo, con Gemma 4 y Qwen 3.5 verificados por SHA-256.
- Niveles de hardware del 0 al 3, propuestos según la memoria y la gráfica.
- Servidor propio compatible con OpenAI (Ollama, LM Studio…), y OpenAI, Claude y OpenRouter opcionales por tarea, con respaldo y panel de actividad.
- Si usas tu propio servidor, game-pal lo encuentra solo y elige un modelo que quepa en tu equipo.
- Los errores de los modelos se explican con qué hacer (servidor cerrado, modelo sin descargar, puerto ocupado, clave no válida…).

### Instalar

- Asistente de primer uso en cuatro pasos: juego, modelo de IA (motor de game-pal según el equipo, servidor propio o nube, con prueba), comprobación de que todo funciona y voz.
- Desde el código basta `uv run game-pal`: compila la GUI solo. `game-pal doctor` revisa el equipo y explica cómo arreglar lo que falte; docs/problemas.md recoge los problemas frecuentes.
- Detecta el juego abierto y cambia a su perfil; los idiomas se toman del sistema la primera vez.
- GUI más sencilla: menú agrupado, guardado automático, opciones avanzadas plegadas, «Lo que falta» en Inicio y barra de progreso en las descargas.
- Instaladores para Windows x64 y ARM64, macOS Apple Silicon y Linux x64, con icono en la bandeja.
- Atestación de procedencia (Sigstore) y sumas SHA-256 en cada versión.

### Para crear perfiles

- `game-pal new-profile` crea el esqueleto de un perfil válido.
- Guía completa en docs/profiles.md: señales, vocabulario, avisos, prompts, fuentes, lectores de guardado y cómo compartirlo.
