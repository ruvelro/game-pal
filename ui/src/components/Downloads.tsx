import type { LiveState } from "../store";

/** Barra de las descargas en curso (modelos de voz, de búsqueda…), para que no parezca que se ha colgado. */
export function Downloads({ live }: { live: LiveState }) {
  const active = Object.values(live.statuses).filter(
    (status) => status && status.state === "starting" && status.detail.startsWith("Descargando"),
  );
  if (active.length === 0) return null;
  return (
    <div className="downloads" role="status">
      {active.map((status) =>
        status ? (
          <div key={status.component} className="download">
            <span className="small">{status.detail}</span>
            <div className={status.progress == null ? "bar indeterminate" : "bar"}>
              <span style={{ width: `${Math.round((status.progress ?? 0.3) * 100)}%` }} />
            </div>
          </div>
        ) : null,
      )}
    </div>
  );
}
