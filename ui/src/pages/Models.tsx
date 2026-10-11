import { useCallback, useEffect, useState } from "react";
import { api, type AppConfig, type LocalServer } from "../api";
import { Card, Field, PageHeader, SaveBar } from "../components/ui";
import { t, type Key } from "../i18n";
import { useConfig } from "../store";

type Cloud = "openai" | "anthropic" | "openrouter";
type ProviderName = "local" | Cloud;
type Role = "reasoning" | "fast" | "vision";

const CLOUDS: Array<[Cloud, string, string]> = [
  ["openai", "OpenAI", "https://platform.openai.com/api-keys"],
  ["anthropic", "Anthropic (Claude)", "https://platform.claude.com/settings/keys"],
  ["openrouter", "OpenRouter", "https://openrouter.ai/keys"],
];
const PRESETS: Array<[string, Key]> = [
  ["local", "models.preset.local"],
  ["backup", "models.preset.backup"],
  ["hybrid", "models.preset.hybrid"],
  ["cloud", "models.preset.cloud"],
];
const ROLES: Array<[Role, Key, Key]> = [
  ["reasoning", "models.role.reasoning", "models.role.reasoning.hint"],
  ["fast", "models.role.fast", "models.role.fast.hint"],
  ["vision", "models.role.vision", "models.role.vision.hint"],
];
function ModelPicker({
  value,
  models,
  onChange,
  placeholder,
  recommended,
}: {
  value: string;
  models: string[] | undefined;
  onChange: (v: string) => void;
  placeholder?: string;
  recommended?: string | null;
}) {
  if (models && models.length > 0) {
    return (
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">{placeholder ?? "—"}</option>
        {models.map((m) => (
          <option key={m} value={m}>
            {m === recommended ? `${m} · ${t("models.recommended")}` : m}
          </option>
        ))}
      </select>
    );
  }
  return <input value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} spellCheck={false} />;
}

function CloudCard({
  id,
  label,
  link,
  configured,
  config,
  setConfig,
  onKey,
}: {
  id: Cloud;
  label: string;
  link: string;
  configured: boolean;
  config: AppConfig;
  setConfig: (c: AppConfig) => void;
  onKey: () => void;
}) {
  const [key, setKey] = useState("");
  const [models, setModels] = useState<string[]>();
  const [error, setError] = useState<string | null>(null);
  const cloud = config.cloud[id];
  const update = (patch: Partial<typeof cloud>) =>
    setConfig({ ...config, cloud: { ...config.cloud, [id]: { ...cloud, ...patch } } });

  const saveKey = async () => {
    setError(null);
    try {
      await api.put(`/api/providers/${id}/key`, { key: key.trim() });
      setKey("");
      onKey();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const findModels = async () => {
    setError(null);
    try {
      setModels((await api.get<{ models: string[] }>(`/api/providers/${id}/models`)).models);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <Card title={label}>
      <div className="row wrap">
        <span className={`chip-static ${configured ? "" : "warn"}`}>
          {configured ? t("models.key.ok") : t("models.key.missing")}
        </span>
        <a className="small" href={link} target="_blank" rel="noreferrer noopener">
          {t("models.key.get")}
        </a>
      </div>
      <Field label={t("models.key")} hint={t("models.key.hint")}>
        <div className="row">
          <input type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} placeholder="sk-…" />
          <button onClick={() => void saveKey()} disabled={key.trim().length < 8}>
            {t("common.save")}
          </button>
          {configured && (
            <button onClick={() => void api.del(`/api/providers/${id}/key`).then(onKey)}>{t("kn.remove")}</button>
          )}
        </div>
      </Field>
      {configured && (
        <>
          <button onClick={() => void findModels()}>{t("models.load")}</button>
          <Field label={t("models.role.reasoning")}>
            <ModelPicker
              value={cloud.model}
              models={models}
              onChange={(model) => update({ model })}
              placeholder={id === "anthropic" ? "claude-opus-5-5" : ""}
            />
          </Field>
          <Field label={t("models.role.fast")}>
            <ModelPicker
              value={cloud.fast_model}
              models={models}
              onChange={(fast_model) => update({ fast_model })}
              placeholder={id === "anthropic" ? "claude-haiku-5-5" : t("models.same")}
            />
          </Field>
          <Field label={t("models.role.vision")}>
            <ModelPicker
              value={cloud.vision_model}
              models={models}
              onChange={(vision_model) => update({ vision_model })}
              placeholder={t("models.same")}
            />
          </Field>
        </>
      )}
      {error && <p className="error-text small">{error}</p>}
    </Card>
  );
}

export function Models() {
  const { config, setConfig, save, error, saved } = useConfig();
  const [keys, setKeys] = useState<Record<Cloud, boolean>>({ openai: false, anthropic: false, openrouter: false });
  const [summary, setSummary] = useState("");
  const [localModels, setLocalModels] = useState<string[]>();
  const [localError, setLocalError] = useState<string | null>(null);
  const [servers, setServers] = useState<LocalServer[]>();

  const loadProviders = useCallback(() => {
    api
      .get<{ keys: Record<Cloud, boolean>; summary: string }>("/api/providers")
      .then((data) => {
        setKeys(data.keys);
        setSummary(data.summary);
      })
      .catch(() => undefined);
  }, []);
  useEffect(loadProviders, [loadProviders]);

  const discover = useCallback(() => {
    setServers(undefined);
    api
      .get<{ servers: LocalServer[] }>("/api/providers/local/discover")
      .then((data) => setServers(data.servers))
      .catch(() => setServers([]));
  }, []);
  useEffect(discover, [discover]);

  if (!config) return null;
  const provider = config.provider;
  const updateLocal = (patch: Partial<typeof provider>) => setConfig({ ...config, provider: { ...provider, ...patch } });
  const current = servers?.find((server) => server.url === provider.base_url.replace(/\/+$/, ""));
  const shownModels = localModels ?? current?.models;
  const chooseServer = (server: LocalServer) => {
    const model = server.models.includes(provider.model) ? provider.model : (server.recommended ?? "");
    updateLocal({ base_url: server.url, model });
    setLocalModels(server.models);
    setLocalError(null);
  };
  const firstCloud = (CLOUDS.find(([id]) => keys[id])?.[0] ?? null) as Cloud | null;

  const applyPreset = (preset: string) => {
    const cloud: ProviderName = firstCloud ?? "local";
    const r = (main: ProviderName, fallback: ProviderName | "none" = "none") => ({ provider: main, fallback });
    const routing =
      preset === "local"
        ? { reasoning: r("local"), fast: r("local"), vision: r("local") }
        : preset === "backup"
          ? { reasoning: r("local", cloud), fast: r("local", cloud), vision: r("local") }
          : preset === "hybrid"
            ? { reasoning: r(cloud, "local"), fast: r("local", cloud), vision: r("local") }
            : { reasoning: r(cloud, "local"), fast: r(cloud, "local"), vision: r(cloud, "local") };
    setConfig({ ...config, routing });
  };

  const setRoute = (role: Role, patch: Partial<AppConfig["routing"][Role]>) =>
    setConfig({ ...config, routing: { ...config.routing, [role]: { ...config.routing[role], ...patch } } });

  const findLocal = async () => {
    setLocalError(null);
    if (await save(config)) {
      try {
        setLocalModels((await api.get<{ models: string[] }>("/api/providers/local/models")).models);
      } catch (e) {
        setLocalError((e as Error).message);
      }
    }
  };

  const onSave = async () => {
    if (await save(config)) loadProviders();
  };
  const usesCloud = Object.values(config.routing).some(
    (route) => route.provider !== "local" || (route.fallback !== "none" && route.fallback !== "local"),
  );

  const providerOptions = (
    <>
      <option value="local">{t("models.local")}</option>
      {CLOUDS.map(([id, name]) => (
        <option key={id} value={id} disabled={!keys[id]}>
          {name}
        </option>
      ))}
    </>
  );

  return (
    <>
      <PageHeader title={t("models.title")} subtitle={t("models.subtitle")} />
      <Card title={t("models.routing")}>
        <p className="muted small">{summary}</p>
        <div className="row wrap">
          {PRESETS.map(([id, label]) => (
            <button key={id} className="chip" onClick={() => applyPreset(id)} disabled={id !== "local" && !firstCloud}>
              {t(label)}
            </button>
          ))}
        </div>
        {!firstCloud && <p className="muted small">{t("models.preset.needkey")}</p>}
        <table className="candidates routes">
          <thead>
            <tr>
              <th>{t("models.task")}</th>
              <th>{t("models.main")}</th>
              <th>{t("models.fallback")}</th>
            </tr>
          </thead>
          <tbody>
            {ROLES.map(([role, label, hint]) => (
              <tr key={role}>
                <td>
                  <strong>{t(label)}</strong>
                  <div className="muted small">{t(hint)}</div>
                </td>
                <td>
                  <select
                    value={config.routing[role].provider}
                    onChange={(e) => setRoute(role, { provider: e.target.value as ProviderName })}
                    aria-label={`${t(label)} · ${t("models.main")}`}
                  >
                    {providerOptions}
                  </select>
                </td>
                <td>
                  <select
                    value={config.routing[role].fallback}
                    onChange={(e) => setRoute(role, { fallback: e.target.value as ProviderName | "none" })}
                    aria-label={`${t(label)} · ${t("models.fallback")}`}
                  >
                    <option value="none">{t("models.fallback.none")}</option>
                    {providerOptions}
                  </select>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {usesCloud && <p className="notice small">{t("models.cloud.privacy")}</p>}
        <label className="check">
          <input
            type="checkbox"
            checked={config.cloud.allow_images}
            onChange={(e) => setConfig({ ...config, cloud: { ...config.cloud, allow_images: e.target.checked } })}
          />
          {t("models.images")}
        </label>
        <SaveBar onSave={() => void onSave()} saved={saved} error={error} />
      </Card>

      <Card title={t("models.local.title")}>
        <p className="muted small">{t("models.subtitle.local")}</p>
        <Field label={t("models.url")}>
          <div className="row">
            <input value={provider.base_url} onChange={(e) => updateLocal({ base_url: e.target.value })} spellCheck={false} />
            <button onClick={() => void findLocal()}>{t("models.load")}</button>
          </div>
        </Field>
        <div className="row wrap">
          <span className="muted small">{t("models.detected")}:</span>
          {servers?.map((server) => (
            <button
              key={server.url}
              className={server.url === current?.url ? "chip active" : "chip"}
              onClick={() => chooseServer(server)}
            >
              {server.name} · {server.models.length} {t("models.detect.count")}
            </button>
          ))}
          <button className="chip" onClick={discover} disabled={!servers}>
            {t("models.detect")}
          </button>
        </div>
        {servers?.length === 0 && <p className="notice small">{t("models.detect.none")}</p>}
        {servers && servers.length > 0 && !current && !localModels && (
          <p className="muted small">{t("models.notresponding")}</p>
        )}
        <div className="two-col tight">
          <Field label={t("models.role.reasoning")}>
            <ModelPicker
              value={provider.model}
              models={shownModels}
              recommended={current?.recommended}
              onChange={(model) => updateLocal({ model })}
            />
          </Field>
          <Field label={t("models.fast")} hint={t("models.fast.hint")}>
            <ModelPicker
              value={provider.fast_model}
              models={shownModels}
              onChange={(fast_model) => updateLocal({ fast_model })}
            />
          </Field>
          <Field label={t("models.vision")} hint={t("models.vision.hint")}>
            <ModelPicker
              value={provider.vision_model}
              models={shownModels}
              onChange={(vision_model) => updateLocal({ vision_model })}
            />
          </Field>
        </div>
        {shownModels?.length === 0 && <p className="muted">{t("models.none")}</p>}
        {localError && <p className="error-text">{localError}</p>}
        <SaveBar onSave={() => void onSave()} saved={saved} error={error} />
      </Card>

      <div className="two-col">
        {CLOUDS.map(([id, label, link]) => (
          <CloudCard
            key={id}
            id={id}
            label={label}
            link={link}
            configured={keys[id]}
            config={config}
            setConfig={setConfig}
            onKey={loadProviders}
          />
        ))}
      </div>
      <SaveBar onSave={() => void onSave()} saved={saved} error={error} />
    </>
  );
}
