# Crear un perfil de juego

Un perfil le enseña a game-pal a reconocer las pantallas de un juego y a sacar de ellas lo importante. Es una carpeta con un YAML; no hace falta programar.

```
profiles/mi-juego/
  profile.yaml        identidad, pantallas y fuentes
  vocabulary.yaml     nombres que conviene reconocer (opcional)
  prompts/advisor.md  cómo debe hablar el compañero (opcional)
```

Los perfiles del usuario van en la carpeta de datos de game-pal, dentro de `profiles/`. Uno con el mismo `id` que un perfil incluido lo sustituye.

Añade esta línea al principio de `profile.yaml` y tu editor te autocompletará y avisará de errores:

```yaml
# yaml-language-server: $schema=../profile.schema.json
```

## Lo mínimo

```yaml
id: mi-juego            # minúsculas, dígitos y guiones; igual que el nombre de la carpeta
name: Mi Juego
game:
  process: [MiJuego]    # nombre del ejecutable, sin ruta ni extensión
  window_title: Mi Juego
screens: []
```

Con esto game-pal ya encuentra la ventana, lee el texto y puede contestar preguntas. Las pantallas son lo que le permite entender qué está pasando.

## Pantallas sin coordenadas

Cada pantalla se reconoce por **señales**: qué texto aparece y cómo se relaciona con el resto. Nunca por posiciones en píxeles, así que un perfil funciona a cualquier resolución.

Lo único espacial que existe son **bandas** de un tercio de la pantalla: `top`, `middle`, `bottom` en vertical y `left`, `center`, `right` en horizontal. Una lista de bandas es su intersección: `[top, right]` es la esquina superior derecha.

### Señales disponibles

| Señal | Se cumple cuando |
| --- | --- |
| `text_matches` | Alguna línea casa con `pattern` (regex) o con un conjunto del `vocabulary`. `min_count` exige varias. |
| `repeated_rows` | Hay al menos `min` filas distintas con ese texto: listas de opciones, tiendas, inventarios. |
| `text_below` / `text_above` | Una línea `target` está justo debajo o encima de una `anchor`. |
| `same_row` | `anchor` y `target` están en la misma fila. |
| `count_at_least` | Hay al menos `lines` líneas de texto en la región. |
| `absent` | Ninguna línea casa con la consulta. |
| `color_dominant` | Una fracción de la región tiene un color. Útil para pantallas sin texto. |

Un nodo de señales exige **todas** sus condiciones. Para combinar usa `all_of`, `any_of` (basta una) y `none_of` (ninguna).

```yaml
screens:
  - id: shop
    description: Tienda del pueblo
    signals:
      any_of:
        - text_matches: { pattern: "^(shop|tienda)$", region: top }
        - repeated_rows: { min: 3, vocabulary: items }
      none_of:
        - text_matches: { vocabulary: pause_menu, region: top }
```

Las expresiones regulares se aplican al texto en minúsculas, con y sin tildes: `bendicion` casa con «Bendición».

### Extraer datos

Una pantalla reconocida puede extraer **listas** (una entrada por opción) y **valores** sueltos.

```yaml
    extract:
      lists:
        options:
          anchor: { vocabulary: rarity }       # cada fila con una rareza es una opción
          min_items: 2
          fields:
            rarity: { source: anchor, vocabulary: rarity }
            name: { source: same_row, vocabulary: items }
            effect: { source: below, max_lines: 3 }
      values:
        selected: { vocabulary: items, region: middle }
```

`source` dice dónde buscar cada campo: en la propia línea ancla, en otra de su misma fila o en las líneas que hay debajo hasta la siguiente opción. Si un campo usa un vocabulario que no está cargado, se guarda el texto tal cual.

### Visión

`vision` decide cuándo se pide ayuda al modelo de visión, si el usuario ha elegido uno:

- `never`: solo señales.
- `on_uncertain` (por defecto): cuando la pantalla es dudosa o la extracción está incompleta.
- `always`: cada vez que aparece. Útil para pantallas con poco texto.

La respuesta del modelo se valida: un nombre con vocabulario debe existir en él y cualquier otro texto debe aparecer en lo que leyó el OCR. Lo demás se descarta.

## Vocabulario

El vocabulario corrige errores de OCR, traduce nombres y limita lo que un modelo puede nombrar.

```yaml
# vocabulary.yaml
sets:
  rarity:
    - id: rare
      names: { en: Rare, es: Raro }
  bosses:
    - id: hecate
      names: { en: Hecate, es: Hécate }
      aliases: { en: [Witch of the Crossroads] }
  pause_menu: [Paused, Pausa]           # forma corta: lista de textos
```

La coincidencia tolera errores típicos de OCR según la longitud: las palabras de hasta 4 letras deben coincidir exactas, y las más largas admiten una o dos letras mal leídas.

### Nombres desde el propio juego

Si el juego guarda sus textos en ficheros legibles, game-pal puede sacar de ahí los nombres exactos en todos sus idiomas. Hoy se admite SJSON:

```yaml
game:
  install_paths:
    windows: ["%ProgramFiles(x86)%/Steam/steamapps/common/Mi Juego"]
    macos: ["~/Library/Application Support/Steam/steamapps/common/Mi Juego"]
    linux: ["~/.local/share/Steam/steamapps/common/Mi Juego"]
vocabulary:
  files: [vocabulary.yaml]
  game_text:
    - set: items
      path: "Content/Text/{lang}/Items.{lang}.sjson"
      id_pattern: "Item$"
  language_codes: { pt: pt-BR }
```

Si el juego no está en esas rutas, el usuario elige la carpeta en la GUI. Las rutas nunca pueden salir de la carpeta del juego.

## Estado de la partida

Las pantallas pueden decir qué significan sus listas para el estado de la partida:

```yaml
    evidence: offered   # ofertas: el jugador aún no las tiene
    # evidence: owned   # inventarios o listas de lo que ya se tiene
```

Con `offered`, si el jugador dice «he cogido X» y X es una de las opciones, game-pal lo apunta como elección suya.

### Leer el guardado

Si el guardado del juego se puede leer, un plugin da la fuente más fiable de todas. Declara el lector y dónde buscar los guardados:

```yaml
evidence:
  save_reader: plugin:MiLector
  save_paths:
    windows: ["%USERPROFILE%/Saved Games/Mi Juego"]
    macos: ["~/Library/Application Support/Mi Juego"]
    linux: ["~/.local/share/Mi Juego"]
```

Y en `plugin.py`, una clase con dos métodos:

```python
from pathlib import Path
from gamepal.plugins import SaveFact, SaveSnapshot


class MiLector:
    def find(self, folder: Path) -> Path | None:
        """El guardado en uso dentro de la carpeta."""
        ...

    def read(self, path: Path) -> SaveSnapshot:
        """Solo lectura: nunca escribas en el guardado ni ejecutes nada del juego."""
        return SaveSnapshot(
            path=str(path),
            saved_at=...,
            run_id="perfil:partida",
            in_run=True,
            facts=[SaveFact("weapon", "weapon", "EspadaLarga")],
            summary={},
        )
```

`gamepal.evidence.formats` trae decodificadores seguros de LZ4 y LuaBins. El plugin de Hades II es un ejemplo completo.

Los plugins son código que se ejecuta en el equipo del jugador. Si instalas un perfil de otra persona, game-pal no cargará su plugin hasta que confíes en él desde la página **Juego**, y volverá a preguntar si el fichero cambia.

## Datos exactos: que el modelo no se invente cifras

Los modelos pequeños, y más sin pensar antes de responder, se inventan cifras y fallan con las cuentas. Un perfil puede darles hechos que no tienen que recordar ni calcular:

```yaml
# Tablas YAML con datos del juego. game-pal busca en ellas lo que se pregunta o se ve (en cualquier idioma,
# singular o plural) y se lo da al modelo, con las fechas ya calculadas.
reference: [reference/crops.yaml]

# Con el día del guardado, game-pal da hecho cuántos días quedan de estación.
calendar:
  seasons: [spring, summer, fall, winter]
  days_per_season: 28

knowledge:
  # Ficha de lo que se ve o se pregunta, buscada al momento por su nombre exacto (MediaWiki), primero en
  # la wiki del idioma del juego.
  lookup:
    - api: https://es.stardewvalleywiki.com/mediawiki/api.php
      language: es
    - api: https://stardewvalleywiki.com/mediawiki/api.php
      language: en
```

Cada fila de una tabla lleva `names` (por idioma) y los datos. Las de cultivos admiten `seasons`, `growth_days`, `regrow_days`, `growth_days_irrigated`, `trellis`, `sell_price` y `seed` (con `names` y `prices` por tienda). Con `keywords`, una pregunta que hable del tema («¿qué planto?») trae las filas de la estación en curso aunque no se nombre ninguna; con `events`, las tiendas de un solo día (un festival) dicen si ya pasaron.

Las tablas de Stardew Valley se generan desde la wiki oficial con `uv run python scripts/stardew_reference.py`: no se escriben a mano.

## Probar un perfil

1. `uv run game-pal profiles` valida todos los perfiles y avisa de vocabularios que se usan sin declarar.
2. En la GUI, página **Juego**, el botón **Probar** evalúa todas las pantallas sobre la última lectura y explica cuáles cumplen y por qué fallan las demás.
3. Prueba tu perfil con capturas reales en distintas resoluciones y, si puedes, en varios idiomas.
