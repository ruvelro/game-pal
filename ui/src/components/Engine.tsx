import { useCallback, useEffect, useState } from "react";
import { api, formatBytes, type DownloadProgress, type EngineStatus } from "../api";
import { t, type Key } from "../i18n";
import type { LiveState } from "../store";

const ROLE_LABEL: Record<string, Key> = {
  reasoning: "models.role.reasoning",
  fast: "models.role.fast",
  vision: "models.role.vision",
};

/** Estado del motor local, que se recarga cuando llega un evento de descarga o de estado. */
export function useEngine(live: LiveState) {
  const [engine, setEngine] = useState<EngineStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const reload = useCallback(() => {
    api
      .get<EngineStatus>("/api/engine")
      .then((data) => {
        setEngine(data);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, []);
  const finished = Object.values(live.downloads).filter((d) => ["done", "error", "cancelled"].includes(d.state)).length;
  const runtimeState = live.statuses.runtime?.state;
  useEffect(reload, [reload, finished, runtimeState]);
  return { engine, reload, error, setError };
}

export function Progress({ item }: { item: DownloadProgress }) {
  const ratio = item.total ? Math.min(1, item.received / item.total) : 0;
  const label: Record<DownloadProgress["state"], string> = {
    queued: t("engine.dl.queued"),
    running: `${Math.round(ratio * 100)} % · ${formatBytes(item.received)} / ${formatBytes(item.total)}`,
    verifying: t("engine.dl.verifying"),
    done: t("engine.dl.done"),
    error: item.error,
    cancelled: t("engine.dl.cancelled"),
  };
  return (
    <div className={`download ${item.state}`}>
      <div className="row between">
        <strong>{item.label}</strong>
        <span className={`small ${item.state === "error" ? "error-text" : "muted"}`}>{label[item.state]}</span>
      </div>
      <div
        className="progress"
        role="progressbar"
        aria-label={item.label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(ratio * 100)}
      >
        <span style={{ width: `${(item.state === "done" ? 1 : ratio) * 100}%` }} />
      </div>
    </div>
  );
}

/** Descargas en curso: primero lo que llega en vivo, si no, lo que informa el servidor. */
export function Downloads({ live, engine }: { live: LiveState; engine: EngineStatus | null }) {
  const items = Object.keys(live.downloads).length ? Object.values(live.downloads) : (engine?.downloads ?? []);
  if (!items.length) return null;
  const busy = items.some((d) => ["queued", "running", "verifying"].includes(d.state));
  return (
    <div className="downloads">
      {items.map((item) => (
        <Progress key={item.item} item={item} />
      ))}
      {busy && (
        <button className="small-button" onClick={() => void api.post("/api/engine/cancel")}>
          {t("engine.cancel")}
        </button>
      )}
    </div>
  );
}

/** Lista del catálogo con instalar y quitar, y el estado del motor. */
export function EnginePanel({ live, onChange }: { live: LiveState; onChange?: () => void }) {
  const { engine, reload, error, setError } = useEngine(live);
  if (!engine) return error ? <p className="error-text">{error}</p> : null;
  const run = async (action: () => Promise<unknown>) => {
    setError(null);
    try {
      await action();
      reload();
      onChange?.();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const stateKey = `engine.state.${engine.state}` as Key;

  return (
    <div className="engine">
      <div className="row wrap between">
        <span className="chip-static">
          llama.cpp {engine.tag} · {engine.variant ?? t("engine.unsupported")}
        </span>
        <span className="small muted">
          {t(stateKey)}
          {engine.port ? ` · 127.0.0.1:${engine.port}` : ""}
        </span>
        {!engine.installed && engine.variant && (
          <button disabled={engine.busy} onClick={() => void run(() => api.post("/api/engine/install", { models: [] }))}>
            {t("engine.install")} ({formatBytes(engine.runtime_size)})
          </button>
        )}
        {engine.installed && (
          <button disabled={engine.busy} onClick={() => void run(() => api.post("/api/engine/restart"))}>
            {t("engine.restart")}
          </button>
        )}
      </div>
      {engine.state === "error" && <pre className="error-text small log">{engine.detail}</pre>}
      <Downloads live={live} engine={engine} />
      <table className="candidates catalog">
        <thead>
          <tr>
            <th>{t("engine.model")}</th>
            <th>{t("engine.uses")}</th>
            <th className="num">{t("engine.size")}</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {engine.models.map((model) => (
            <tr key={model.id}>
              <td>
                <strong>{model.name}</strong>
                {engine.running_models.includes(model.id) && <span className="chip-static ok small-chip">{t("engine.ready")}</span>}
                <div className="muted small">
                  {model.description} · {model.license}
                </div>
              </td>
              <td className="small">{model.roles.map((r) => t(ROLE_LABEL[r] ?? "models.role.reasoning")).join(", ")}</td>
              <td className="num small">{formatBytes(model.size)}</td>
              <td className="num">
                {model.installed ? (
                  <button
                    className="small-button"
                    disabled={engine.busy}
                    onClick={() => void run(() => api.del(`/api/engine/models/${model.id}`))}
                  >
                    {t("kn.remove")}
                  </button>
                ) : (
                  <button
                    className="small-button"
                    disabled={engine.busy}
                    onClick={() => void run(() => api.post("/api/engine/install", { models: [model.id] }))}
                  >
                    {t("engine.download")}
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {error && <p className="error-text small">{error}</p>}
    </div>
  );
}
