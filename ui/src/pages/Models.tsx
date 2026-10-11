import { useState } from "react";
import { api } from "../api";
import { Card, Field, PageHeader, SaveBar } from "../components/ui";
import { t } from "../i18n";
import { useConfig } from "../store";

const PRESETS: Array<[string, string]> = [
  ["Ollama", "http://127.0.0.1:11434/v1"],
  ["LM Studio", "http://127.0.0.1:1234/v1"],
  ["llama.cpp", "http://127.0.0.1:8080/v1"],
  ["MLX", "http://127.0.0.1:8081/v1"],
];

export function Models() {
  const { config, setConfig, save, error, saved } = useConfig();
  const [models, setModels] = useState<string[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  if (!config) return null;
  const provider = config.provider;
  const update = (patch: Partial<typeof provider>) => setConfig({ ...config, provider: { ...provider, ...patch } });

  const findModels = async () => {
    setLoading(true);
    setLoadError(null);
    // Guarda primero la dirección para que el daemon consulte el servidor correcto.
    if (await save(config)) {
      try {
        const data = await api.get<{ models: string[] }>("/api/provider/models");
        setModels(data.models);
      } catch (e) {
        setModels(null);
        setLoadError((e as Error).message);
      }
    }
    setLoading(false);
  };

  return (
    <>
      <PageHeader title={t("models.title")} subtitle={t("models.subtitle")} />
      <Card>
        <Field label={t("models.url")}>
          <div className="row">
            <input value={provider.base_url} onChange={(e) => update({ base_url: e.target.value })} spellCheck={false} />
            <button onClick={() => void findModels()} disabled={loading}>
              {t("models.load")}
            </button>
          </div>
        </Field>
        <div className="row wrap">
          <span className="muted small">{t("models.presets")}:</span>
          {PRESETS.map(([name, url]) => (
            <button key={name} className="chip" onClick={() => update({ base_url: url })}>
              {name}
            </button>
          ))}
        </div>
        <Field label={t("models.model")}>
          {models && models.length > 0 ? (
            <select value={provider.model} onChange={(e) => update({ model: e.target.value })}>
              <option value="">—</option>
              {models.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          ) : (
            <input value={provider.model} onChange={(e) => update({ model: e.target.value })} spellCheck={false} />
          )}
        </Field>
        {models?.length === 0 && <p className="muted">{t("models.none")}</p>}
        {loadError && (
          <p className="error-text" role="alert">
            {loadError}
          </p>
        )}
        <SaveBar onSave={() => void save(config)} saved={saved} error={error} />
      </Card>
    </>
  );
}
