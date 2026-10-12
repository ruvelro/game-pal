import { useState } from "react";
import { api, type ConnectionReport, type ConnectionStepId } from "../api";
import { Field } from "./ui";
import { t, type Key } from "../i18n";

const STEP_TITLE: Record<ConnectionStepId, Key> = {
  address: "conn.step.address",
  connect: "conn.step.connect",
  server: "conn.step.server",
  model: "conn.step.model",
  reply: "conn.step.reply",
};

export type ConnectionTarget = "local" | "openai" | "anthropic" | "openrouter";

/**
 * Prueba la conexión con el modelo paso a paso, con lo que hay escrito aunque no esté guardado:
 * dirección, conexión, servidor, modelo y una respuesta de verdad. Sirve igual para este equipo, otro
 * de la red o la nube.
 */
export function ConnectionTest({
  target,
  baseUrl,
  model,
  serverKey,
  onModels,
}: {
  target: ConnectionTarget;
  baseUrl?: string;
  model?: string;
  serverKey?: string;
  onModels?: (models: string[]) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState<ConnectionReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    setBusy(true);
    setReport(null);
    setError(null);
    try {
      const result = await api.post<ConnectionReport>("/api/providers/test", {
        target,
        base_url: baseUrl ?? "",
        model: model ?? "",
        key: serverKey ?? "",
      });
      setReport(result);
      if (result.models.length > 0) onModels?.(result.models);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="connection-test">
      <button className="primary" onClick={() => void run()} disabled={busy}>
        {busy ? t("conn.testing") : t("conn.test")}
      </button>
      {report && (
        <>
          <p className={report.ok ? "ok-text small" : "error-text small"} role="status">
            {report.ok ? t("conn.ok") : t("conn.failed")}
          </p>
          <ul className="check-list">
            {report.steps.map((step) => (
              <li key={step.id}>
                <span className={`dot ${step.ok ? "ok" : "bad"}`} aria-hidden="true" />
                <div className="check-body">
                  <span>
                    <strong>{t(STEP_TITLE[step.id])}</strong> <span className="muted">· {step.detail}</span>
                    {step.ms !== null && <span className="muted small"> · {step.ms} ms</span>}
                  </span>
                  {!step.ok && step.fix && <span className="small">{step.fix}</span>}
                </div>
              </li>
            ))}
          </ul>
        </>
      )}
      {error && <p className="error-text small">{error}</p>}
    </div>
  );
}

/** Clave del servidor propio, para los que la piden (vLLM, LM Studio con clave…). Va al almacén del sistema. */
export function ServerKey({
  configured,
  value,
  onChange,
  onSaved,
}: {
  configured: boolean;
  value: string;
  onChange: (key: string) => void;
  onSaved: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const save = async () => {
    setError(null);
    try {
      await api.put("/api/providers/server/key", { key: value.trim() });
      onChange("");
      onSaved();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  return (
    <Field label={t("models.server.key")} hint={configured ? t("models.key.ok") : t("models.server.key.hint")}>
      <div className="row">
        <input
          type="password"
          autoComplete="off"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={configured ? "••••••••" : ""}
        />
        <button onClick={() => void save()} disabled={value.trim().length < 8}>
          {t("common.save")}
        </button>
        {configured && (
          <button onClick={() => void api.del("/api/providers/server/key").then(onSaved)}>{t("kn.remove")}</button>
        )}
      </div>
      {error && <span className="error-text small">{error}</span>}
    </Field>
  );
}
