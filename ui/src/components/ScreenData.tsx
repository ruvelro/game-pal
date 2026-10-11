import type { FieldValue, ScreenDetected } from "../api";

function text(value: FieldValue | undefined): string {
  if (value === undefined) return "";
  return typeof value === "string" ? value : value.name;
}

function title(value: FieldValue | undefined): string | undefined {
  return value && typeof value !== "string" && value.seen !== value.name ? `OCR: ${value.seen}` : undefined;
}

/** Muestra los datos extraídos de una pantalla: listas como tarjetas y valores sueltos como pares. */
export function ScreenData({ data }: { data: ScreenDetected["data"] }) {
  const entries = Object.entries(data);
  if (entries.length === 0) return null;
  return (
    <div className="screen-data">
      {entries.map(([key, value]) =>
        Array.isArray(value) ? (
          <ol className="options" key={key}>
            {value.map((item, i) => (
              <li key={i}>
                <div className="row option-head">
                  <strong title={title(item.name)}>{text(item.name) || "—"}</strong>
                  {item.rarity && (
                    <span className={`rarity r-${typeof item.rarity === "string" ? "x" : item.rarity.id}`}>
                      {text(item.rarity)}
                    </span>
                  )}
                </div>
                {Object.entries(item)
                  .filter(([field]) => field !== "name" && field !== "rarity")
                  .map(([field, fieldValue]) => (
                    <p className="muted small" key={field} title={title(fieldValue)}>
                      {text(fieldValue)}
                    </p>
                  ))}
              </li>
            ))}
          </ol>
        ) : (
          <p key={key}>
            <span className="muted small">{key}: </span>
            <strong title={title(value)}>{text(value)}</strong>
          </p>
        ),
      )}
    </div>
  );
}
