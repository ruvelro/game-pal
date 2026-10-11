# game-pal · Especificación

Versión 0.1 · 11 de octubre de 2026 · Estado: borrador aprobado para empezar a construir.

## 1. Visión

game-pal es un compañero de juego con IA que mira la pantalla, escucha al jugador y le aconseja mientras juega. Funciona con modelos locales por defecto, sin enviar nada fuera del ordenador, y puede usar proveedores en la nube si el jugador lo decide. No está atado a un juego: cada juego es un perfil de datos que cualquiera puede crear, y el conocimiento se carga desde wikis y guías por URL.

### Principios

1. **Local primero.** Todo lo esencial funciona sin conexión y sin clave de API. La nube es opcional, por tarea, y siempre visible para el jugador.
2. **Privado por defecto.** Nada sale del equipo salvo que el jugador active un proveedor remoto. Sin telemetría. Secretos en el almacén de credenciales del sistema.
3. **El jugador manda.** game-pal nunca pulsa teclas ni mueve el ratón en el juego. Solo observa, escucha y aconseja.
4. **Evidencia antes que suposición.** Lo que el modelo cree ver tiene menos rango que lo que dice el jugador, y esto menos que lo que dice el fichero de guardado.
5. **El modelo elige, el código redacta.** Siempre que se pueda, el modelo devuelve una opción de una lista validada y el texto lo compone código determinista.
6. **Nada depende de coordenadas.** Las pantallas se reconocen por su texto y su estructura, no por posiciones fijas.
7. **Juego, idioma y modelo son configuración, no código.**

## 2. Alcance

### Fase 1 (v0.1 · MVP utilizable)

- Windows x64, Windows ARM64 nativo, macOS Apple Silicon, Linux x64.
- Captura de pantalla, OCR multiidioma, detección de cambios y clasificación de pantalla.
- Modelo de visión local opcional según nivel de hardware.
- Activación por palabra clave, push-to-talk (teclado y mando) y modo proactivo.
- Salida por overlay, texto en la GUI y voz local.
- Perfiles de juego declarativos. Perfil de Hades II completo (incluido lector de guardado) y perfil de Stardew Valley básico.
- Conocimiento por URL con búsqueda semántica local.
- Proveedores: local compatible con OpenAI, llama-server gestionado, OpenAI, Anthropic, OpenRouter. Modo híbrido por tarea.
- Memoria de sesión opcional.
- GUI completa para configurar y controlar todo.
- Panel de actividad local/nube.

### Fase 2

- Historial de partidas, resumen de sesión, exportación HTML y PNG.
- Panel remoto desde móvil o segundo equipo (misma red).
- Editor visual de perfiles de juego.
- Marketplace de perfiles por repositorio Git.

### Fuera de alcance

- Controlar el juego o inyectar entradas.
- Leer memoria del proceso del juego o inyectar código.
- Integraciones de streaming (Twitch, OBS).
- Telemetría o cuentas de usuario.

## 3. Plataformas y hardware

### Sistemas

| Sistema | Captura | Overlay | Audio | Secretos | Aceleración |
| --- | --- | --- | --- | --- | --- |
| Windows x64 / ARM64 | Windows.Graphics.Capture (DXGI); en M1, Win32 + BitBlt del área cliente | Ventana topmost click-through excluida de captura | WASAPI | Credential Manager | CUDA, Vulkan, DirectML, CPU |
| macOS ARM | ScreenCaptureKit | NSPanel flotante sin foco, nivel por encima del juego | CoreAudio | Keychain | Metal (llama.cpp / MLX) |
| Linux x64 | PipeWire/portal (Wayland), X11 fallback; en M1, solo X11 y XWayland | Capa wlr-layer-shell o ventana override-redirect en X11 | PipeWire/PulseAudio | Secret Service | CUDA, Vulkan, ROCm, CPU |

Requisito de captura: el juego debe ir en **ventana sin bordes o ventana**. La pantalla completa exclusiva impide dibujar encima en los tres sistemas; la GUI lo avisa y propone voz, panel en segundo monitor o panel remoto.

### Niveles de hardware

El asistente de configuración detecta RAM, GPU y VRAM y propone un nivel. El jugador puede cambiarlo.

| Nivel | Requisito | Visión | Razonamiento | Voz |
| --- | --- | --- | --- | --- |
| 0 · Ligero | Cualquier equipo | Solo OCR y reglas del perfil | Modelo de texto 1-4B o nube | Whisper tiny/base, Piper |
| 1 · Básico | 8 GB RAM, GPU integrada | VLM 2-4B (Qwen3-VL 2B/4B, Gemma 3 4B) | Texto 4-8B | Whisper small, Piper |
| 2 · Medio | 16 GB RAM o 8 GB VRAM | VLM 7-12B | Texto 8-14B | Whisper medium, Kokoro |
| 3 · Alto | 32 GB+ o 16 GB+ VRAM | VLM 27-35B | Texto 27-70B | Whisper large-v3-turbo, Kokoro |

En el nivel 0 el OCR hace todo el reconocimiento y el razonamiento recae en un modelo de texto (local pequeño o nube). Es el camino que garantiza que game-pal sea útil en un portátil modesto.

## 4. Arquitectura

```
┌─────────────────────────────────────────────────────────────────────┐
│  GUI (web local)      Overlay nativo      Bandeja      Panel remoto │
└───────────────┬─────────────────┬─────────────┬─────────────────────┘
                │   WebSocket / IPC local        │
┌───────────────┴─────────────────┴─────────────┴─────────────────────┐
│                         Núcleo (daemon)                             │
│  ┌──────────┐ ┌───────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ │
│  │ Captura  │→│ Percepción│→│  Estado  │→│Razonamien│→│  Salida  │ │
│  │ pantalla │ │ OCR+VLM   │ │ evidencia│ │ to+router│ │ ovl/txt/ │ │
│  └──────────┘ └───────────┘ └──────────┘ └──────────┘ │ voz      │ │
│  ┌──────────┐ ┌───────────┐ ┌──────────┐ ┌──────────┐ └──────────┘ │
│  │ Audio in │→│ Wake/PTT  │→│   STT    │ │Conocimien│               │
│  │          │ │           │ │          │ │to (RAG)  │               │
│  └──────────┘ └───────────┘ └──────────┘ └──────────┘               │
│  ┌──────────────────┐ ┌──────────────┐ ┌──────────────────────────┐ │
│  │ Perfiles de juego│ │   Memoria    │ │ Proveedores de modelos   │ │
│  └──────────────────┘ └──────────────┘ └──────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
```

Un único proceso daemon con módulos desacoplados por un bus de eventos interno. La GUI es una app web servida en loopback. El overlay y la bandeja son procesos nativos ligeros que hablan con el daemon por WebSocket local autenticado con token.

### 4.1 Captura

- Captura la **ventana del juego**, no el monitor, identificada por el perfil (nombre de proceso, título de ventana o elección manual). Así no entran en la captura ventanas privadas.
- Frecuencia adaptativa: 2 fps en reposo, hasta 5 fps cuando hay cambios, 0 cuando el juego no está en primer plano.
- Detección de cambio por hash perceptual (dHash 16×16) sobre la imagen reducida. Umbrales en el perfil, con valores por defecto sensatos.
- Las ventanas de game-pal se excluyen de la captura (Windows: `WDA_EXCLUDEFROMCAPTURE`; macOS: filtro de ventanas de ScreenCaptureKit; Linux: se captura la ventana del juego, no la pantalla).

### 4.2 Percepción

Tres etapas en cascada, cada una solo si la anterior lo justifica:

1. **OCR** con PaddleOCR (modelos ONNX multiidioma, CPU/GPU) como motor por defecto. Alternativas enchufables: Apple Vision en macOS, Windows OCR en Windows, Tesseract. Devuelve líneas con texto, caja normalizada y confianza.
2. **Clasificador de pantalla** que evalúa las **señales declarativas** del perfil (sección 5). Decide el tipo de pantalla y extrae entidades (nombres de opciones, rarezas, cantidades) sin coordenadas: usa relaciones relativas (encima de, debajo de, alineado con, en la misma fila), agrupación por proximidad y coincidencia difusa contra el vocabulario del perfil.
3. **Modelo de visión** solo cuando el perfil lo pide para ese tipo de pantalla o cuando el clasificador no está seguro. Recibe la imagen, las líneas OCR y un esquema JSON estricto. Nunca puede nombrar una entidad que no esté en el vocabulario del perfil o en el texto OCR.

Debounce de dos lecturas coincidentes antes de confirmar una pantalla y dos lecturas vacías antes de darla por cerrada.

### 4.3 Estado y evidencia

Un registro por sesión de hechos con origen y rango:

| Rango | Origen | Ejemplo |
| --- | --- | --- |
| 1 | Fichero de guardado | "Arma equipada: Bastón, aspecto Melinoë, boons: …" |
| 2 | Declaración del jugador | "He cogido el de Zeus" |
| 3 | Confirmación visual repetida | Mismo ítem visto en dos frames separados ≥200 ms con texto legible |
| 4 | Oferta visible | "En pantalla se ofrecen A, B y C" |
| 5 | Inferencia del modelo | "Probablemente el jugador va a build de rayo" |

Reglas: una oferta nunca implica posesión; un hecho de rango superior supersede a los inferiores; los hechos caducan al cambiar de partida; todo hecho guarda hora, origen y la evidencia textual.

Implementación (M5):
- **Guardado (rango 1)**: el lector del perfil se ejecuta cada vez que el fichero cambia (sondeo cada 2 s). Si la suma de comprobación no cuadra, se asume que el juego está escribiendo y se reintenta. Cada lectura sustituye por completo los hechos del guardado anteriores.
- **Jugador (rango 2)**: una frase que no es pregunta ni negación y nombra exactamente una de las opciones ofrecidas en pantalla («he cogido el Latigazo») se registra como elección. Si nombra varias o ninguna, no se adivina.
- **Visto (rango 3)**: pantallas declaradas con `evidence: owned` (inventarios, listas de lo que se tiene), ya confirmadas por dos lecturas.
- **Ofrecido (rango 4)**: pantallas con `evidence: offered`; se guardan aparte y el modelo las recibe marcadas como «todavía no lo tiene».
- El modelo recibe el estado con el origen de cada dato y la instrucción de que el guardado manda.

Los lectores de guardado son plugins del perfil. Son de solo lectura, abren el fichero en modo compartido, verifican integridad y nunca ejecutan código del juego (los guardados en Lua se decodifican, no se evalúan).

### 4.4 Razonamiento y enrutador de modelos

Roles de modelo, cada uno con su proveedor configurable:

| Rol | Función | Local por defecto |
| --- | --- | --- |
| `vision` | Leer pantallas complejas | Qwen3-VL o Gemma 3 según nivel |
| `reasoning` | Responder preguntas y aconsejar | Qwen3 / Gemma 3 / Llama según nivel |
| `fast` | Clasificaciones y respuestas cortas | Modelo pequeño 1-4B |
| `embeddings` | Búsqueda semántica del conocimiento | bge-m3 o multilingual-e5-small (ONNX) |
| `stt` | Transcribir voz | faster-whisper |
| `tts` | Hablar | Piper o Kokoro |
| `wake` | Palabra clave | openWakeWord (ONNX) |

Proveedores: `local-openai` (cualquier endpoint compatible: Ollama, LM Studio, llama-server, vLLM, MLX), `managed-llama` (llama-server descargado y lanzado por game-pal en loopback con clave aleatoria), `openai`, `anthropic`, `openrouter`. Añadir proveedor es implementar una interfaz de cuatro métodos: `chat`, `chat_stream`, `embed`, `capabilities`.

Políticas híbridas por rol, elegibles en la GUI:

- **Solo local**: nunca sale nada.
- **Local, nube si falla**: la nube solo entra con error o timeout local.
- **Nube para razonar, local para mirar**: la visión y el OCR en local, la pregunta y el contexto a la nube.
- **Nube para todo lo que se pida**: lo proactivo en local, las preguntas del jugador en la nube.

Implementación (M6): cada rol (`reasoning`, `fast`, `vision`) tiene un proveedor principal y un respaldo opcional en `routing`; la GUI ofrece cuatro preajustes (solo local, local con respaldo en la nube, híbrido y nube) y permite ajustar cada rol a mano. El respaldo entra cuando el principal falla, nunca después de haber mostrado parte de una respuesta, y usa su propio modelo para esa tarea. OpenAI y OpenRouter comparten el cliente compatible con OpenAI; Claude usa el SDK oficial de Anthropic, con `claude-opus-5-5` para razonar y `claude-haiku-5-5` para lo rápido por defecto, esfuerzo bajo o medio según la longitud de la respuesta y el respaldo del servidor ante rechazos activado en los modelos que lo admiten. Las capturas solo van a la nube si el jugador lo permite expresamente (`cloud.allow_images`). Las claves se guardan con `keyring` en el almacén del sistema y la API nunca las devuelve.

Cada petición lleva etiqueta de rol, proveedor, tokens y latencia, y aparece en el panel de actividad. El registro vive en SQLite local; el coste usa el que informa el proveedor (OpenRouter) o una estimación por tarifa marcada como tal. Antes de enviar una captura a un proveedor remoto por primera vez en la sesión, la GUI lo avisa.

Modelos que razonan: lo que piensan antes de responder cuenta en el límite de tokens. Cada petición lleva el largo de la respuesta y, aparte, el margen para pensar (`generation` en la configuración: largo de respuesta, si se deja pensar y cuánto). Las tareas internas (enrutar, palabras clave, visión) nunca piensan. game-pal averigua si el modelo razona: Ollama lo dice en `/api/show`, y con otros servidores se nota en la primera respuesta. El margen solo se suma si el modelo razona, para no alargar las respuestas de los que no lo hacen, y el razonamiento se apaga cuando el servidor lo permite (`reasoning_effort` en Ollama, `chat_template_kwargs` en llama.cpp, vLLM y MLX, `reasoning.enabled` en OpenRouter). Si una respuesta se queda sin margen antes de decir nada, se repite una vez sin pensar (o con el doble de margen si no se puede apagar). Si tampoco responde, se avisa: nunca se deja una respuesta en blanco.

Presupuesto de contexto: las instrucciones del sistema se componen por bloques con caché de prompt cuando el proveedor lo soporte; el registro de evidencia se resume de forma incremental para mantenerse bajo un límite de tokens configurable; las capturas se recortan a la ventana del juego y se reescalan a un ancho máximo por rol.

### 4.5 Conocimiento

- **Fuentes**: URLs (páginas, wikis enteras con profundidad configurable, sitemaps), ficheros locales (Markdown, TXT, HTML, PDF) y los ficheros de texto del propio juego cuando el perfil sabe dónde están (vocabulario, M2).
- **Descarga segura**: solo http(s) hacia direcciones públicas (también tras cada redirección, para que un enlace no pueda apuntar a la red local), respeto de `robots.txt`, una petición por segundo y servidor por defecto, límite de tamaño por página y tipos de contenido permitidos. Ruido típico de wikis (páginas especiales, historiales, usuarios, ficheros) excluido por defecto.
- **Ingesta**: extracción del contenido principal con trafilatura (conserva tablas y títulos), limpieza de navegación, troceado por secciones a unos 900 caracteres, embeddings locales y SQLite con FTS5. Los vectores se guardan junto a cada fragmento y se buscan por fuerza bruta con numpy: con miles de fragmentos por juego tarda milisegundos y evita extensiones nativas de SQLite.
- **Embeddings**: multilingual-e5-small cuantizado en ONNX (118 MB, revisión fijada), el servidor de modelos del usuario o ninguno (solo palabras).
- **Búsqueda**: híbrida por fusión de rangos (BM25 con más peso al título, y vectores con umbral propio de cada modelo). Antes de buscar, el modelo propone palabras clave, incluidos los nombres en el idioma de las fuentes: así «¿cuánto tardan las chirivías?» encuentra la página «Parsnip» aunque el modelo de embeddings pequeño no relacione ambos idiomas. A la consulta se añaden los nombres de lo que hay en pantalla.
- **Actualización**: cada documento guarda hash, ETag y Last-Modified; al actualizar se usan peticiones condicionales y solo se reprocesa lo que cambió. Botón «Actualizar» por fuente y global; comprobación periódica según el perfil (`refresh`), pero solo de fuentes que el usuario ya descargó alguna vez: nada se descarga sin pedirlo.
- **Atribución**: el contexto va numerado y el modelo cita con [n]; la GUI muestra las fuentes enlazadas y la voz no lee ni números de cita ni URLs.
- **Vocabulario**: los títulos de las páginas descargadas se añaden al vocabulario del perfil y ayudan a corregir el OCR.
- Las fuentes son datos no fiables: el contenido se envuelve como información y nunca como instrucciones para el modelo.

### 4.6 Interacción

Activadores, combinables:

- **Palabra clave**: la voz se detecta con Silero VAD y cada fragmento corto se transcribe con Whisper, que busca la frase al principio con tolerancia a errores. Así el jugador elige cualquier frase en cualquier idioma («oye compañero», «hey pal») sin entrenar modelos, y puede decir la pregunta seguida. Las frases se pasan a Whisper como pista para que las escriba bien. openWakeWord queda como alternativa futura para equipos muy justos, porque solo transcribe cuando hay voz.
- **Push-to-talk** con tecla global (pynput) o botón de mando (SDL2, también con el juego en primer plano). Mantener para hablar o pulsar para alternar. La GUI tiene además un botón «mantén para hablar».
- **Proactivo**: el perfil declara en qué pantallas game-pal puede hablar sin que se le pregunte, con su enfriamiento y una vez por entrada en la pantalla. Un atajo global los silencia y reactiva.
- **Texto** en la GUI y, en fase 2, en el panel remoto.

Flujo de una pregunta por voz: tono de listo → grabación hasta 0,7 s de silencio → transcripción local → enrutado «rápido / razonado» por un modelo pequeño opcional (sin modelo rápido, todo va razonado) → respuesta en streaming → voz desde la primera frase, mientras se sintetiza la siguiente → «gracias», «para» o la palabra clave interrumpen la voz y la generación.

### 4.7 Salida

- **Overlay**: tarjeta nativa, click-through, no activable, posicionable por esquina o arrastre, con tamaño y opacidad configurables, paginación automática y cierre cuando la pantalla cambia. Tipos: consejo, comparación de opciones, aviso proactivo, estado (escuchando, pensando).
- **Texto**: en la GUI, con histórico de la sesión y fuentes citadas.
- **Voz**: TTS local en el idioma de salida configurado, con control de volumen, velocidad y ducking opcional del audio del juego.

El jugador elige por tipo de evento qué salidas se usan.

### 4.8 Memoria

- Desactivada por defecto. Al activarla, cada sesión (desde que arranca game-pal o se cambia de perfil) guarda sus preguntas y respuestas y un resumen generado al cerrar. Las sesiones sin conversación se descartan.
- Memoria entre sesiones opcional: lecciones que el jugador pide guardar ("recuerda que…") y el resumen de las últimas N sesiones, inyectados como datos.
- Todo en SQLite local, exportable y borrable desde la GUI con un botón.

### 4.9 Multiidioma

Tres idiomas independientes, configurables por perfil y por sesión:

- **Idioma del juego**: determina los modelos OCR y el vocabulario (una fuente de conocimiento puede estar en otro idioma; el modelo traduce).
- **Idioma del jugador** (entrada): modelos de wake word y STT.
- **Idioma de salida**: respuestas, overlay y voz TTS.

El OCR y los modelos de visión y texto son multiidioma; las señales de pantalla de un perfil pueden declarar patrones por idioma o patrones estructurales independientes del idioma. La GUI está traducida (español e inglés en v0.1).

## 5. Perfiles de juego

Un perfil es una carpeta:

```
profiles/hades-2/
  profile.yaml        # identidad, ventana, idiomas, señales, salidas, fuentes
  vocabulary.yaml     # entidades conocidas (opcional, se autogenera desde las fuentes)
  prompts/            # bloques de instrucciones específicos del juego (Markdown)
  plugin.py           # opcional: lector de guardado y hooks
  knowledge/          # fuentes locales opcionales
```

### 5.1 `profile.yaml` (extracto)

La guía completa, con todas las señales y opciones, está en [profiles.md](profiles.md). Extracto del perfil de Hades II:

```yaml
id: hades-2
name: Hades II
game:
  process: [Hades2, Hades II]
  window_title: Hades II
  languages_supported: [en, es, fr, de, it, pt, ja, ko, zh, ru, pl]
  install_paths:
    windows: ["%ProgramFiles(x86)%/Steam/steamapps/common/Hades II"]
vocabulary:
  files: [vocabulary.yaml]
  game_text:
    - set: boons
      path: "Content/Game/Text/{lang}/TraitText.{lang}.sjson"
      id_pattern: "Boon$"
screens:
  - id: boon_choice
    description: Elección de bendición entre varias opciones
    signals:
      any_of:
        - repeated_rows: { min: 2, vocabulary: boons }
        - all_of:
            - text_matches: { vocabulary: boon_menu_titles, region: top }
            - repeated_rows: { min: 2, vocabulary: rarity }
      none_of:
        - text_matches: { vocabulary: reading_menus, region: top }
    extract:
      lists:
        options:
          anchor: { vocabulary: rarity }
          fields:
            rarity: { source: anchor, vocabulary: rarity }
            name: { source: same_row, vocabulary: boons }
            effect: { source: below, max_lines: 3 }
    vision: on_uncertain
    proactive: { enabled: true, cooldown_seconds: 6, task: compare_options }
    outputs: [overlay, voice]
knowledge:
  sources:
    - url: https://hades.fandom.com/wiki/Hades_II
      depth: 1
```

### 5.2 Señales disponibles

`text_matches`, `repeated_rows`, `text_below`, `text_above`, `same_row`, `count_at_least`, `absent` y `color_dominant`, combinables con `all_of`, `any_of` y `none_of`. La única noción espacial son bandas de un tercio (`top`, `middle`, `bottom`, `left`, `center`, `right`); una lista de bandas es su intersección. Los perfiles se validan contra un JSON Schema publicado en `profiles/profile.schema.json`, y `game-pal profiles` avisa de vocabularios usados sin declarar.

Una pantalla se confirma con dos lecturas coincidentes, o con una sola si la puntuación es alta y no hay otra candidata cercana. Se da por cerrada tras dos lecturas sin ella; mientras tanto se pide releer la pantalla aunque no haya cambiado.

### 5.3 Plugins

`plugin.py` expone clases que implementan interfaces pequeñas. En M5 existe `SaveReader` (`find(carpeta)` y `read(ruta) -> SaveSnapshot`); `ScreenHook` y `KnowledgeHook` quedan para más adelante. Un plugin es código que se ejecuta dentro de game-pal: los de perfiles incluidos son de confianza y los del usuario solo se cargan si este confía expresamente en ese fichero, identificado por su SHA-256; si cambia, se vuelve a preguntar. Un perfil sin plugin funciona íntegramente con el YAML.

### 5.4 Perfiles incluidos en v0.1

- **Hades II**: pantallas de bendición, martillo, poma, recuerdo, puertas, presentación de jefe, victoria y derrota; lector de guardado (SGB1, Adler-32, LZ4, LuaBins) para arma, aspecto, arcanas, bendiciones con nivel y rareza, recuerdos y sala.
- **Stardew Valley**: pantallas de elección de profesión, resumen del día, tienda de semillas, diálogo y menús; lector del guardado XML (sin DTD ni entidades) para día, estación, tiempo de mañana, suerte, dinero, habilidades, mochila, cónyuge y amistades; fuente de la wiki oficial, que se descarga solo cuando el jugador lo pide.
- **Genérico**: sin señales; solo OCR, VLM y conocimiento por URL. Es el punto de partida para crear un perfil nuevo.

## 6. GUI

App web local (React + Vite, servida por el daemon) empaquetada con ventana nativa (pywebview) y bandeja del sistema. Objetivo: que cualquier persona configure game-pal sin tocar ficheros.

Páginas:

1. **Inicio**: estado en una mirada (juego detectado, perfil activo, modelos cargados, micrófono, nivel de hardware), botón grande "Empezar" y "Pausar", último consejo y campo de pregunta por texto.
2. **Asistente de primer uso**: detecta hardware, propone nivel, descarga modelos con barra de progreso, prueba micrófono, altavoz, captura y overlay en cuatro pasos.
3. **Juegos**: perfiles instalados, importar desde carpeta o URL de repositorio, crear nuevo desde el genérico, editar YAML con validación en línea, probar señales contra una captura.
4. **Conocimiento**: fuentes por perfil, añadir URL o fichero, estado de la ingesta, fecha de actualización, buscar en el índice para comprobar qué sabe.
5. **Modelos**: por cada rol, proveedor y modelo; catálogo descargable con tamaño y requisitos; estado del runtime gestionado; claves de API (se guardan en el almacén del sistema y el campo se vacía).
6. **Voz y activación**: palabra clave, push-to-talk de teclado y mando con detección de pulsación, VAD, voz de salida con prueba, idiomas de entrada y salida.
7. **Salida**: overlay (posición, tamaño, opacidad, vista previa), texto, voz, qué eventos usan cada salida, modo proactivo y su silencio rápido.
8. **Memoria**: activar, ver sesiones, exportar, borrar.
9. **Actividad**: peticiones por rol y proveedor, local frente a nube, latencias, tokens y coste estimado para proveedores de pago.
10. **Ajustes**: idioma de la GUI, inicio con el sistema, atajos globales, carpeta de datos, diagnóstico y exportación de registros anonimizados.

Overlay y bandeja: la bandeja ofrece iniciar, pausar, silenciar proactivo, abrir GUI y salir.

## 7. Seguridad y privacidad

- Secretos solo en Keychain, Credential Manager o Secret Service, nunca en ficheros ni en la línea de comandos. Los procesos hijos reciben claves por variable de entorno limpia.
- El daemon escucha solo en loopback con token aleatorio por arranque. El panel remoto de fase 2 requiere emparejamiento explícito con código.
- Todo endpoint local debe ser loopback; los remotos solo HTTPS con certificado válido.
- Todo contenido externo (texto OCR, fuentes de conocimiento, ficheros de guardado, transcripciones) se trata como datos no fiables y se delimita en los prompts.
- Las salidas estructuradas se validan contra esquemas antes de usarse; nombres fuera del vocabulario se rechazan.
- Captura solo de la ventana del juego. Aviso antes de enviar la primera captura a un proveedor remoto.
- Perfiles y plugins de terceros muestran un resumen de permisos al instalarse; los plugins solo pueden leer las rutas declaradas.
- Sin telemetría. Los registros de diagnóstico se guardan en local, rotan y pueden exportarse con un botón que elimina rutas de usuario y transcripciones.
- Dependencias fijadas con hashes; escaneo de vulnerabilidades en CI; releases firmadas (Sigstore) con sumas SHA-256.

## 8. Stack técnico

| Capa | Elección |
| --- | --- |
| Lenguaje | Python 3.12, tipado estricto (mypy), formateo y lint con ruff |
| Núcleo asíncrono | asyncio, bus de eventos interno, pydantic para modelos de datos y validación de perfiles |
| Captura | Windows: `windows-capture`; macOS: ScreenCaptureKit vía PyObjC; Linux: portal xdg-desktop-portal + PipeWire, X11 fallback |
| OCR | PaddleOCR en ONNX Runtime (CPU, CUDA, DirectML, CoreML); backends alternativos enchufables |
| Hash perceptual | imagehash / implementación propia con numpy |
| Modelos locales | llama.cpp (llama-server) gestionado o cualquier endpoint OpenAI-compatible; descarga desde Hugging Face con verificación de hash |
| Embeddings | ONNX Runtime con multilingual-e5-small cuantizado, o el servidor de modelos |
| Índice | SQLite con FTS5 y vectores en BLOB buscados con numpy |
| STT | faster-whisper (CTranslate2) |
| TTS | Piper (ONNX) y Kokoro (ONNX) |
| Wake word | Silero VAD + Whisper con frases configurables; openWakeWord opcional en el futuro |
| VAD | Silero VAD (ONNX) |
| Audio | sounddevice (PortAudio) |
| Mando y atajos | SDL2 (pysdl2 + pysdl2-dll) para mandos; pynput para teclas globales |
| Overlay | Proceso aparte con ventana nativa: NSPanel (PyObjC) en macOS, Win32 por ctypes en Windows, Tk como respaldo en Linux |
| GUI | React + TypeScript + Vite, estilos con tokens de diseño, servida por el daemon (FastAPI + WebSocket) y envuelta con pywebview |
| Empaquetado | PyInstaller por plataforma; instaladores MSIX/MSI, DMG notarizado, AppImage y Flatpak; actualizador opcional con verificación de firma |
| Tests | pytest, fixtures sintéticas de capturas y guardados, snapshot tests de prompts, tests de contrato por proveedor con grabaciones |
| CI | GitHub Actions en Windows x64/ARM64, macOS ARM y Linux x64: lint, tipos, tests, build y escaneo de dependencias |

Estructura del repositorio:

```
game-pal/
  gamepal/                # paquete Python del núcleo
    capture/  perception/  evidence/  reasoning/  knowledge/
    interaction/  output/  memory/  profiles/  providers/  gui_server/
  overlay/                # cliente nativo ligero del overlay
  ui/                     # app web (React)
  profiles/               # perfiles incluidos (hades-2, stardew-valley, generic)
  models/                 # catálogo de modelos descargables (YAML), sin pesos
  assets/                 # icono y recursos
  docs/                   # esta spec, guías de usuario y de creación de perfiles
  scripts/                # utilidades de build
  tests/
```

## 9. Calidad

- Cobertura mínima del 80 % en percepción, evidencia, perfiles y proveedores.
- Fixtures sintéticas: capturas generadas por código con texto conocido para probar OCR y señales sin material del juego.
- Evaluación de perfiles: cada perfil incluye un conjunto de capturas anonimizadas (aportadas por el autor del perfil) con la clasificación esperada; CI lo ejecuta.
- Evaluación de modelos: conjunto de preguntas y respuestas esperadas por perfil para comparar modelos locales y nube en calidad y latencia desde la GUI.
- Latencia objetivo en nivel 2: confirmar pantalla < 700 ms, consejo proactivo en overlay < 4 s, primera palabra hablada tras la pregunta < 3 s.

## 10. Roadmap

| Hito | Contenido |
| --- | --- |
| M1 · Esqueleto ✅ | Daemon, bus, GUI básica, captura en los tres sistemas, OCR, hash perceptual, perfil genérico, proveedor local |
| M2 · Entender ✅ | Señales declarativas, clasificador, vocabulario, VLM opcional, perfil Hades II sin guardado |
| M3 · Hablar ✅ | Wake word, PTT teclado y mando, VAD, STT, TTS, overlay, enrutado rápido/razonado |
| M4 · Saber ✅ | Ingesta por URL y ficheros, índice híbrido, citas, actualización |
| M5 · Recordar ✅ | Lector de guardado de Hades II, modelo de evidencia completo, memoria por sesión |
| M6 · Abrir ✅ | Proveedores nube, modo híbrido, panel de actividad, claves en almacén del sistema, Stardew Valley |
| M7 · Pulir | Asistente de primer uso, catálogo de modelos, instaladores, firmas, docs de creación de perfiles, v0.1 |

## 11. Riesgos y mitigaciones

| Riesgo | Mitigación |
| --- | --- |
| OCR lento en CPU | Modelos ONNX ligeros, frecuencia adaptativa, recorte a zonas con cambios |
| Overlay no visible en algún compositor Linux | Fallback a ventana flotante normal y a voz; documentar compositores probados |
| Modelos locales pequeños aconsejan mal | Tareas con esquema cerrado, vocabulario restringido, evaluación por perfil desde la GUI, nube opcional por rol |
| Wikis cambian de estructura | Extractor de contenido principal genérico más reglas por dominio opcionales; fecha y hash por fuente |
| Parches del juego cambian textos | Vocabulario regenerado desde fuentes y ficheros del juego; señales difusas; nada hardcodeado en código |
| Windows ARM64 con poco soporte de librerías | Ruedas ONNX Runtime y llama.cpp ARM64 existen; CI dedicado para detectar roturas |
| Empaquetado Python pesado | Descarga de modelos bajo demanda; binarios base < 300 MB |
