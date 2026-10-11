# Problemas frecuentes

Lo primero, en cualquier caso:

```bash
uv run game-pal doctor
```

Revisa todo lo necesario y dice qué falla y cómo arreglarlo. Lo mismo aparece en la GUI, en Inicio › Lo que falta, y en el asistente de primer uso (Avanzado › Abrir el asistente de configuración).

## Arrancar

**«Falta compilar la interfaz».** game-pal compila la GUI al arrancar, pero necesita [Node.js](https://nodejs.org) 22 o superior. Instálalo y vuelve a arrancar.

**La pestaña dice que no tiene el token de sesión.** Por seguridad, la dirección de la GUI lleva un token que cambia en cada arranque. Usa la dirección completa (con `#token=…`) que aparece en la terminal, o vuelve a arrancar game-pal para que abra la GUI.

**«La configuración no es válida».** Se ha editado `config.yaml` a mano con algún valor fuera de rango. Corrígelo o bórralo: game-pal creará uno nuevo con los valores por defecto. `game-pal doctor` dice dónde está.

## El modelo de IA

**«No hay ningún servidor de modelos en marcha».** game-pal necesita un modelo de IA para aconsejar. Lo más sencillo es instalar [Ollama](https://ollama.com), abrirlo y descargar un modelo:

```bash
ollama pull qwen3:4b
```

game-pal lo detecta solo en unos segundos, sin reiniciar. También valen LM Studio, llama.cpp o MLX, o una clave de OpenAI, Claude u OpenRouter en la página Modelos.

**«En http://127.0.0.1:11434/v1 responde otro programa».** Otro programa ocupa el puerto de Ollama (o el de LM Studio, llama.cpp o MLX) y contesta con error. Ciérralo y vuelve a abrir tu servidor de modelos, o configura el servidor en otro puerto y escribe su dirección en Modelos › Opciones avanzadas.

**«No tiene el modelo…».** El modelo elegido no está descargado en el servidor. En Ollama: `ollama pull <modelo>`. O elige otro en la página Modelos.

**Las respuestas tardan mucho o se cortan.** El modelo es demasiado grande para tu equipo. Elige uno más pequeño en Modelos; el recomendado está marcado.

**«Se ha quedado pensando» o tarda en empezar a hablar.** Los modelos que razonan (Qwen 3, DeepSeek R1, gpt-oss…) piensan antes de contestar. En Modelos › Cómo responde puedes desactivar «Dejar que piense antes de responder», que es lo más rápido, o darle más margen para pensar en las opciones avanzadas. La página dice si tu modelo razona y si se le puede apagar. El largo de las respuestas se elige en la misma tarjeta.

**«No acepta la clave de API» / «límite de uso».** La clave de la nube no es válida, ha caducado o se ha agotado el saldo. Cámbiala en Modelos o revisa tu cuenta del proveedor.

## Ver el juego

**macOS: «no da permiso de Grabación de pantalla».** Ajustes del Sistema › Privacidad y seguridad › Grabación de pantalla: activa la app desde la que lanzas game-pal (Terminal, iTerm, VS Code…) y vuelve a arrancar game-pal. El botón «Abrir Ajustes» de la GUI te lleva directo.

**El juego no aparece.** Ponlo en modo ventana o ventana sin bordes: la pantalla completa exclusiva no se puede capturar. Si el juego tiene perfil, game-pal lo reconoce solo al abrirlo; si no, elige su ventana en la página Juego.

**Linux con Wayland.** game-pal solo ve las ventanas de X11 y XWayland. Usa una sesión X11 o lanza el juego con XWayland.

**Windows ARM.** RapidOCR no existe para esa arquitectura y se usa Windows OCR, que necesita el paquete del idioma del juego (Configuración › Hora e idioma).

## Voz y atajos

**La primera vez tarda en activarse la voz.** Se descargan los modelos de voz (unos 140 MB con Whisper base). La GUI muestra una barra con el progreso.

**Linux: «Sin audio».** Falta PortAudio: `sudo apt install libportaudio2`.

**macOS: no te oye.** Ajustes del Sistema › Privacidad y seguridad › Micrófono: activa la app desde la que lanzas game-pal.

**Los atajos de teclado no hacen nada.** En macOS hace falta permiso de Accesibilidad (y Monitorización de entrada) para la app desde la que lanzas game-pal; luego vuelve a arrancar. En Wayland los atajos globales no funcionan: usa la voz o los botones de la GUI.
