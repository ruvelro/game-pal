# Cambios

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

### Instalar

- Asistente de primer uso: equipo, modelos, voz y juego en cuatro pasos.
- Instaladores para Windows x64 y ARM64, macOS Apple Silicon y Linux x64, con icono en la bandeja.
- Atestación de procedencia (Sigstore) y sumas SHA-256 en cada versión.

### Para crear perfiles

- `game-pal new-profile` crea el esqueleto de un perfil válido.
- Guía completa en docs/profiles.md: señales, vocabulario, avisos, prompts, fuentes, lectores de guardado y cómo compartirlo.
