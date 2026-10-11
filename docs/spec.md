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

Cada petición lleva etiqueta de rol, proveedor, tokens y latencia, y aparece en el panel de actividad. Antes de enviar una captura a un proveedor remoto por primera vez en la sesión, la GUI lo avisa.

Presupuesto de contexto: las instrucciones del sistema se componen por bloques con caché de prompt cuando el proveedor lo soporte; el registro de evidencia se resume de forma incremental para mantenerse bajo un límite de tokens configurable; las capturas se recortan a la ventana del juego y se reescalan a un ancho máximo por rol.

### 4.5 Conocimiento

- **Fuentes**: URLs (páginas, secciones de wiki, sitemaps), ficheros locales (Markdown, PDF, TXT, HTML) y ficheros de texto del propio juego cuando el perfil sabe dónde están.
- **Ingesta**: rastreo respetando `robots.txt`, profundidad y dominio configurables, extracción de contenido principal (tablas incluidas), limpieza, troceado por secciones, embeddings locales, índice vectorial en SQLite (sqlite-vec) más índice léxico (FTS5). Búsqueda híbrida con re-ranking ligero.
- **Actualización**: cada fuente guarda fecha, hash y versión del juego declarada. Botón "Actualizar conocimiento" por perfil y comprobación programada opcional.
- **Atribución**: cada respuesta puede citar la fuente; en voz no se leen URLs.
- **Vocabulario**: de las fuentes se extrae automáticamente un vocabulario de entidades (nombres de objetos, personajes, habilidades) que alimenta la corrección difusa del OCR y limita lo que el modelo puede nombrar. El perfil puede ampliarlo a mano.
- Las fuentes son datos no fiables: el contenido se envuelve como información y nunca como instrucciones para el modelo.

### 4.6 Interacción

Activadores, combinables:

- **Palabra clave** (openWakeWord, offline, multiidioma mediante modelos entrenados; se incluyen varias por defecto y la GUI permite entrenar una nueva con pocas muestras).
- **Push-to-talk** con tecla global o botón de mando (SDL2 para detectar mandos en los tres sistemas). Mantener para hablar o pulsar para alternar.
- **Proactivo**: el perfil declara en qué pantallas game-pal puede hablar sin que se le pregunte y con qué frecuencia máxima. El jugador puede silenciar lo proactivo con una tecla.
- **Texto** en la GUI y en el panel remoto.

Flujo de una pregunta por voz: tono de listo → grabación con VAD (Silero VAD, con estimación de ruido) → transcripción local → enrutado "rápido / razonado" por un clasificador del modelo `fast`, no por regex → respuesta en streaming → voz desde la primera frase → "gracias" o la palabra clave interrumpen.

### 4.7 Salida

- **Overlay**: tarjeta nativa, click-through, no activable, posicionable por esquina o arrastre, con tamaño y opacidad configurables, paginación automática y cierre cuando la pantalla cambia. Tipos: consejo, comparación de opciones, aviso proactivo, estado (escuchando, pensando).
- **Texto**: en la GUI, con histórico de la sesión y fuentes citadas.
- **Voz**: TTS local en el idioma de salida configurado, con control de volumen, velocidad y ducking opcional del audio del juego.

El jugador elige por tipo de evento qué salidas se usan.

### 4.8 Memoria

- Desactivada por defecto. Al activarla, cada sesión guarda su registro de evidencia, las preguntas y respuestas, y un resumen generado al cerrar.
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

```yaml
id: hades-2
name: Hades II
version: 1
game:
  process: [Hades2, Hades2.exe]
  window_title: "Hades II"
  languages_supported: [en, es, fr, de, it, pt, ja, ko, zh]
  text_files:                     # opcional, para vocabulario y descripciones exactas
    - path: "Content/Game/Text/{lang}/TraitText.{lang}.sjson"
      kind: sjson
screens:
  - id: boon_choice
    description: Elección de bendición entre varias opciones
    signals:                      # todas relativas, ninguna coordenada
      any_of:
        - text_matches: { pattern: "^(boons? of|choose a boon)", region: top, fuzzy: 1 }
        - text_matches: { pattern: "^(bendiciones? de|elige una bendición)", region: top, fuzzy: 1 }
      all_of:
        - repeated_rows: { min: 2, with: { vocabulary: rarity } }
        - text_matches: { vocabulary: confirm_button, region: bottom }
    extract:
      options:
        each: row_with_vocabulary: rarity
        fields:
          name: { nearest_text: left, vocabulary: boons }
          rarity: { vocabulary: rarity }
          effect: { text_below: name, max_lines: 3 }
    vision: on_uncertain          # never | on_uncertain | always
    proactive:
      enabled: true
      cooldown_seconds: 8
      task: compare_options       # tarea predefinida con esquema JSON
    outputs: [overlay, voice]
  - id: boss_intro
    signals:
      all_of:
        - text_matches: { vocabulary: bosses, region: center }
        - text_below: { vocabulary: boss_titles }
    proactive: { enabled: true, task: boss_briefing, once_per_encounter: true }
evidence:
  save_reader: plugin:HadesSaveReader   # opcional
  save_paths:
    windows: "%USERPROFILE%/Saved Games/Hades II"
    macos: "~/Library/Application Support/Supergiant Games/Hades II"
    linux: "~/.local/share/Supergiant Games/Hades II"
knowledge:
  sources:
    - url: https://hades.fandom.com/wiki/Hades_II
      depth: 2
      include: ["/wiki/*"]
    - url: https://www.example-guide.com/hades-2/builds
  refresh: weekly
prompts:
  advisor: prompts/advisor.md
  glossary: prompts/glossary.md
```

### 5.2 Señales disponibles

`text_matches`, `vocabulary`, `repeated_rows`, `text_below`, `text_above`, `same_row`, `count_at_least`, `absent`, `color_dominant` (para pantallas sin texto, como una pantalla de muerte roja). Todas aceptan `region` como banda relativa (`top`, `center`, `bottom`, `left`, `right`) nunca como números. Los perfiles se validan contra un JSON Schema y la GUI muestra errores en línea.

### 5.3 Plugins

`plugin.py` expone clases que implementan interfaces pequeñas: `SaveReader.read(path) -> dict`, `ScreenHook.after_classify(screen, evidence)`, `KnowledgeHook.vocabulary()`. Se ejecutan en el mismo proceso pero con permisos explícitos declarados en el perfil (lectura de rutas concretas). Un perfil sin plugin funciona íntegramente con el YAML.

### 5.4 Perfiles incluidos en v0.1

- **Hades II**: pantallas de bendición, martillo, poma, recuerdo, puertas, presentación de jefe, victoria y derrota; lector de guardado (SGB1, Adler-32, LZ4, LuaBins) para arma, aspecto, arcanas, bendiciones con nivel y rareza, recuerdos y sala.
- **Stardew Valley**: pantallas de inventario, diálogo, tienda y nivel de habilidad; lector del guardado XML para día, estación, dinero, amistades, habilidades y colección; fuentes de la wiki oficial.
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
| Embeddings | ONNX Runtime (bge-m3 / multilingual-e5-small) |
| Índice | SQLite con sqlite-vec y FTS5 |
| STT | faster-whisper (CTranslate2) |
| TTS | Piper (ONNX) y Kokoro (ONNX) |
| Wake word | openWakeWord (ONNX) |
| VAD | Silero VAD (ONNX) |
| Audio | sounddevice (PortAudio) |
| Mando y atajos | SDL2 (pysdl2) para mandos; pynput para teclas globales |
| Overlay | Ventana nativa por plataforma mediante un módulo pequeño con pywebview y ajustes de ventana por plataforma (ctypes / PyObjC / GTK layer-shell) |
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
| M2 · Entender | Señales declarativas, clasificador, vocabulario, VLM opcional, perfil Hades II sin guardado |
| M3 · Hablar | Wake word, PTT teclado y mando, VAD, STT, TTS, overlay, enrutado rápido/razonado |
| M4 · Saber | Ingesta por URL y ficheros, índice híbrido, citas, actualización |
| M5 · Recordar | Lector de guardado de Hades II, modelo de evidencia completo, memoria por sesión |
| M6 · Abrir | Proveedores nube, modo híbrido, panel de actividad, claves en almacén del sistema, Stardew Valley |
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
