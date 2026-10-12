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

**Para saber qué falla, pulsa «Probar conexión»** en Modelos (o en el paso Modelo del asistente). Comprueba la dirección, la conexión, el servidor, el modelo y una respuesta de verdad, y dice en qué paso se para. Sirve igual para un servidor en este equipo, en otro de tu red o en la nube.

**«… responde con un error: …».** Algo contesta en esa dirección, pero con un error, que se muestra tal cual. Puede ser otro programa en el puerto de Ollama (o de LM Studio, llama.cpp o MLX), o tu servidor sin un modelo listo. Si usas un programa que sirve los modelos de otro equipo (NVIDIA PAIR, por ejemplo), comprueba que ese equipo está encendido y conectado: «no active node» o «model inventory unavailable» quieren decir eso.

**El servidor está en otro equipo.** Escribe su dirección en Modelos › Dirección del servidor, por ejemplo `http://192.168.1.20:11434/v1`. Dentro de tu red vale `http://` con la IP o el nombre `.local`; fuera de ella, solo `https://`. El servidor tiene que aceptar conexiones de la red: en Ollama, arráncalo con `OLLAMA_HOST=0.0.0.0`; en LM Studio, activa «Serve on Local Network». Y el cortafuegos de ese equipo debe dejar pasar el puerto. Si el servidor pide clave, escríbela en «Clave del servidor».

**«No tiene el modelo…».** El modelo elegido no está descargado en el servidor. En Ollama: `ollama pull <modelo>`. O elige otro en la página Modelos.

**Las respuestas se inventan días o precios.** game-pal da al modelo el calendario, los datos de cultivos y la ficha de la wiki de lo que se ve, con la orden de no dar cifras que no estén ahí. Si aun así pasa, comprueba en Conocimiento que el perfil tiene sus datos y que hay conexión para buscar fichas.

**Las respuestas tardan mucho o se cortan.** El modelo es demasiado grande para tu equipo. Elige uno más pequeño en Modelos; el recomendado está marcado.

**«Se ha quedado pensando» o tarda en empezar a hablar.** Los modelos que razonan (Qwen 3, DeepSeek R1, gpt-oss…) piensan antes de contestar. En Modelos › Cómo responde puedes desactivar «Dejar que piense antes de responder», que es lo más rápido, o darle más margen para pensar en las opciones avanzadas. La página dice si tu modelo razona y si se le puede apagar. El largo de las respuestas se elige en la misma tarjeta.

**«No acepta la clave de API» / «límite de uso».** La clave de la nube no es válida, ha caducado o se ha agotado el saldo. Cámbiala en Modelos o revisa tu cuenta del proveedor.

## Ver el juego

**macOS: «no da permiso de Grabación de pantalla».** game-pal lo pide al arrancar la primera vez. Si dijiste que no, macOS ya no vuelve a preguntar: ve a Ajustes del Sistema › Privacidad y seguridad › Grabación de pantalla, activa game-pal (o la app desde la que lo lanzas: Terminal, iTerm, VS Code…) y vuelve a arrancarlo. El botón «Abrir Ajustes» de la GUI te lleva directo.

**El juego no aparece.** Ponlo en modo ventana o ventana sin bordes: la pantalla completa exclusiva no se puede capturar. Si el juego tiene perfil, game-pal lo reconoce solo al abrirlo; si no, elige su ventana en la página Juego.

**Linux con Wayland.** game-pal solo ve las ventanas de X11 y XWayland. Usa una sesión X11 o lanza el juego con XWayland.

**Windows ARM.** RapidOCR no existe para esa arquitectura y se usa Windows OCR, que necesita el paquete del idioma del juego (Configuración › Hora e idioma).

## Voz y atajos

**La voz se cerraba con «Error processing file … phontab».** espeak-ng, que usa Piper, no acepta rutas de más de unos 160 caracteres para sus datos. game-pal les da una ruta corta y, si aun así fallara, usa la voz del sistema en lugar de cerrarse.

**Tarda en responder.** En el registro (macOS: `~/Library/Logs/game-pal/game-pal.log`) cada pregunta deja cuánto tardó la transcripción, los datos (fichas de la wiki), las primeras palabras del modelo y la respuesta entera. Si lo lento son las fichas, se responde sin ellas a los 3,5 s; si es el modelo, prueba uno más pequeño o desactiva que piense antes de responder.

**No sabe qué objeto llevo en la mano.** game-pal lee el texto de la pantalla, y el objeto seleccionado no aparece escrito. Dile cuál es («tengo un ramo de flores, ¿a quién se lo doy?») o abre el inventario y pasa el ratón por encima para que salga su nombre.

**La primera vez tarda en activarse la voz.** Se descargan los modelos de voz (unos 140 MB con Whisper base). La GUI muestra una barra con el progreso.

**Linux: «Sin audio».** Falta PortAudio: `sudo apt install libportaudio2`.

**macOS: no te oye.** game-pal pide el micrófono al activar la voz. Si lo negaste: Ajustes del Sistema › Privacidad y seguridad › Micrófono, y activa game-pal o la app desde la que lo lanzas.

**Windows: no te oye.** Configuración › Privacidad y seguridad › Micrófono: deja que las aplicaciones de escritorio usen el micrófono. Windows no deja pedirlo desde game-pal; la GUI abre ese panel.

**Los atajos de teclado no hacen nada.** En macOS hace falta el permiso de Monitorización de entrada (o el de Accesibilidad). game-pal lo pide al arrancar; en cuanto lo concedes, los atajos se activan solos, sin reiniciar. En Wayland los atajos globales no funcionan: usa la voz o los botones de la GUI.

**El chat sobre el juego no aparece.** Se abre con Ctrl+Alt+C (se cambia en Voz y respuestas) o con el botón «Chat sobre el juego» de Inicio. Necesita el overlay activado y el juego en modo ventana o ventana sin bordes: encima de la pantalla completa exclusiva no se puede dibujar. Intro envía, Esc lo cierra y devuelve el teclado al juego.
