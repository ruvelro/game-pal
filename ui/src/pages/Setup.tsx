import { useCallback, useEffect, useState } from "react";
import {
  api,
  type AppConfig,
  type AskResult,
  type Devices,
  type LocalServer,
  type ProfileSummary,
  type SetupCheck,
  type SetupInfo,
  type WindowSummary,
} from "../api";
import { Preview } from "../components/Preview";
import { Field, formatKey } from "../components/ui";
import { LANGUAGES, t, type Key } from "../i18n";
import { useConfig, type LiveState } from "../store";

type Cloud = "openai" | "anthropic" | "openrouter";
type SetConfig = (config: AppConfig) => void;

const STEPS: Key[] = ["setup.step.game", "setup.step.model", "setup.step.check", "setup.step.voice"];
const CLOUDS: Array<[Cloud, string, string]> = [
  ["anthropic", "Anthropic (Claude)", "https://platform.claude.com/settings/keys"],
  ["openai", "OpenAI", "https://platform.openai.com/api-keys"],
  ["openrouter", "OpenRouter", "https://openrouter.ai/keys"],
];
const STATE_DOT: Record<SetupCheck["state"], string> = { ok: "ok", warn: "warn", error: "bad", off: "neutral" };

/** Las comprobaciones de /api/setup, refrescadas cada `intervalMs` si se indica. */
function useSetupInfo(intervalMs?: number) {
  const [info, setInfo] = useState<SetupInfo | null>(null);
  const refresh = useCallback(
    () =>
      api
        .get<SetupInfo>("/api/setup")
        .then(setInfo)
        .catch(() => undefined),
    [],
  );
  useEffect(() => {
    void refresh();
    if (!intervalMs) return;
    const id = window.setInterval(() => void refresh(), intervalMs);
    return () => window.clearInterval(id);
  }, [refresh, intervalMs]);
  return { info, refresh };
}

function CommandBox({ command }: { command: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    await navigator.clipboard.writeText(command).catch(() => undefined);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  };
  return (
    <div className="command">
      <code>{command}</code>
      <button className="chip" onClick={() => void copy()}>
        {copied ? t("setup.check.copied") : t("setup.check.copy")}
      </button>
    </div>
  );
}

export function CheckRow({ check }: { check: SetupCheck }) {
  const fixable = check.state !== "ok";
  return (
    <li>
      <span className={`dot ${STATE_DOT[check.state]}`} aria-hidden="true" />
      <div className="check-body">
        <span>
          <strong>{check.title}</strong> <span className="muted">· {check.detail}</span>
        </span>
        {fixable && check.fix && <span className="small">{check.fix}</span>}
        {fixable && check.command && <CommandBox command={check.command} />}
        {check.settings && (
          <button className="chip" onClick={() => void api.post(`/api/setup/settings/${check.settings}`)}>
            {t("setup.check.settings")}
          </button>
        )}
      </div>
    </li>
  );
}

function GameStep({
  config,
  setConfig,
  reload,
}: {
  config: AppConfig;
  setConfig: SetConfig;
  reload: () => Promise<unknown>;
}) {
  const [profiles, setProfiles] = useState<ProfileSummary[]>([]);
  const [windows, setWindows] = useState<WindowSummary[]>([]);
  const { info, refresh } = useSetupInfo(4000);

  const look = useCallback(async () => {
    await api.post("/api/setup/autodetect").catch(() => undefined);
    await reload();
    void refresh();
    api
      .get<WindowSummary[]>("/api/windows")
      .then(setWindows)
      .catch(() => setWindows([]));
  }, [reload, refresh]);

  useEffect(() => {
    api
      .get<{ profiles: ProfileSummary[] }>("/api/profiles")
      .then((data) => setProfiles(data.profiles))
      .catch(() => undefined);
    void look();
  }, [look]);

  const game = info?.checks.find((c) => c.id === "game");
  const capture = info?.checks.find((c) => c.id === "capture");
  const language = (key: "game_language" | "output_language", label: Key) => (
    <Field label={t(label)}>
      <select value={config[key]} onChange={(e) => setConfig({ ...config, [key]: e.target.value })}>
        {LANGUAGES.map(([code, name]) => (
          <option key={code} value={code}>
            {name}
          </option>
        ))}
      </select>
    </Field>
  );

  return (
    <>
      <h1>{t("setup.game.title")}</h1>
      <p className="muted">{t("setup.game.text")}</p>
      <ul className="check-list">
        {capture && capture.state !== "ok" && <CheckRow check={capture} />}
        {game && <CheckRow check={game} />}
      </ul>
      <div className="two-col tight">
        <Field label={t("game.profile")}>
          <select
            value={config.profile}
            onChange={(e) => setConfig({ ...config, profile: e.target.value, auto_detect_game: false })}
          >
            {profiles.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t("game.window")}>
          <select
            value={config.window_title ?? ""}
            onChange={(e) => setConfig({ ...config, window_title: e.target.value || null })}
          >
            <option value="">{t("game.window.auto")}</option>
            {windows.map((w, i) => (
              <option key={`${w.title}-${i}`} value={w.title}>
                {w.title} — {w.process}
              </option>
            ))}
          </select>
        </Field>
      </div>
      {config.profile === "generic" && !config.window_title && <p className="muted small">{t("setup.game.generic")}</p>}
      <div>
        <button onClick={() => void look()}>{t("setup.game.refresh")}</button>
      </div>
      <div className="two-col tight">
        {language("game_language", "game.lang")}
        {language("output_language", "game.out")}
      </div>
    </>
  );
}

function ModelTest({ config, save }: { config: AppConfig; save: (c: AppConfig) => Promise<boolean> }) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<AskResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const run = async () => {
    setBusy(true);
    setResult(null);
    setError(null);
    try {
      if (await save(config)) setResult(await api.post<AskResult>("/api/provider/ask", { prompt: t("setup.model.prompt") }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="check-body">
      <button className="primary" onClick={() => void run()} disabled={busy}>
        {busy ? t("setup.model.testing") : t("setup.model.test")}
      </button>
      {result && (
        <p className="ok-text">
          «{result.text.trim()}» <span className="muted small">· {result.model}, {result.elapsed_ms} ms</span>
        </p>
      )}
      {error && <p className="error-text">{error}</p>}
    </div>
  );
}

function LocalModel({ config, setConfig }: { config: AppConfig; setConfig: SetConfig }) {
  const [servers, setServers] = useState<LocalServer[]>();
  const discover = useCallback(
    () =>
      api
        .get<{ servers: LocalServer[] }>("/api/providers/local/discover")
        .then((data) => setServers(data.servers))
        .catch(() => setServers([])),
    [],
  );
  useEffect(() => void discover(), [discover]);
  // Mientras no haya ningún servidor, se sigue buscando: así se detecta Ollama en cuanto se abre.
  useEffect(() => {
    if (servers?.length !== 0) return;
    const id = window.setInterval(() => void discover(), 5000);
    return () => window.clearInterval(id);
  }, [servers, discover]);

  const provider = config.provider;
  const current = servers?.find((s) => s.url === provider.base_url.replace(/\/+$/, ""));
  const choose = useCallback(
    (server: LocalServer, model?: string) => {
      const keep = model ?? (server.models.includes(provider.model) ? provider.model : (server.recommended ?? ""));
      setConfig({ ...config, provider: { ...provider, base_url: server.url, model: keep } });
    },
    [config, provider, setConfig],
  );
  // Si el servidor configurado no responde pero hay otro, se propone ese.
  useEffect(() => {
    if (servers && servers.length > 0 && !current && servers[0]) choose(servers[0]);
  }, [servers, current, choose]);

  if (servers === undefined) return <p className="muted">{t("setup.model.searching")}</p>;
  if (servers.length === 0) {
    return (
      <div className="notice">
        <span>{t("setup.model.none")}</span>
        <ol className="steps-text">
          <li>
            {t("setup.model.none.1")}{" "}
            <a href="https://ollama.com" target="_blank" rel="noreferrer noopener">
              ollama.com
            </a>
          </li>
          <li>
            {t("setup.model.none.2")}
            <CommandBox command="ollama pull qwen3:4b" />
          </li>
          <li>{t("setup.model.none.3")}</li>
        </ol>
      </div>
    );
  }
  return (
    <div className="two-col tight">
      <Field label={t("setup.model.server")}>
        <select
          value={current?.url ?? ""}
          onChange={(e) => {
            const server = servers.find((s) => s.url === e.target.value);
            if (server) choose(server);
          }}
        >
          {servers.map((s) => (
            <option key={s.url} value={s.url}>
              {s.name} · {s.models.length} {t("models.detect.count")}
            </option>
          ))}
        </select>
      </Field>
      <Field label={t("setup.model.model")}>
        <select value={provider.model} onChange={(e) => current && choose(current, e.target.value)}>
          <option value="">—</option>
          {current?.models.map((m) => (
            <option key={m} value={m}>
              {m === current.recommended ? `${m} · ${t("models.recommended")}` : m}
            </option>
          ))}
        </select>
      </Field>
    </div>
  );
}

function CloudModel({ config, setConfig }: { config: AppConfig; setConfig: SetConfig }) {
  const routed = config.routing.reasoning.provider;
  const [cloud, setCloud] = useState<Cloud>(routed === "local" ? "anthropic" : routed);
  const [keys, setKeys] = useState<Record<string, boolean>>({});
  const [key, setKey] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const link = CLOUDS.find(([id]) => id === cloud)?.[2];

  const loadKeys = useCallback(
    () =>
      api
        .get<{ keys: Record<string, boolean> }>("/api/providers")
        .then((data) => setKeys(data.keys))
        .catch(() => undefined),
    [],
  );
  useEffect(() => void loadKeys(), [loadKeys]);
  useEffect(() => {
    setModels([]);
    if (!keys[cloud]) return;
    api
      .get<{ models: string[] }>(`/api/providers/${cloud}/models`)
      .then((data) => setModels(data.models))
      .catch((e: Error) => setError(e.message));
  }, [cloud, keys]);

  // Las respuestas van a la nube; si falla, a este equipo. Las capturas se quedan en local.
  const use = (provider: Cloud, model?: string) => {
    const route = { provider, fallback: "local" as const };
    const settings = config.cloud[provider];
    setConfig({
      ...config,
      cloud: { ...config.cloud, [provider]: model === undefined ? settings : { ...settings, model } },
      routing: { reasoning: route, fast: route, vision: { provider: "local", fallback: "none" } },
    });
  };
  useEffect(() => {
    if (keys[cloud] && config.routing.reasoning.provider !== cloud) use(cloud);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cloud, keys]);

  const saveKey = async () => {
    setError(null);
    try {
      await api.put(`/api/providers/${cloud}/key`, { key: key.trim() });
      setKey("");
      await loadKeys();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <>
      <p className="muted small">{t("setup.model.cloud.text")}</p>
      <div className="two-col tight">
        <Field label={t("setup.model.cloud.provider")}>
          <select value={cloud} onChange={(e) => setCloud(e.target.value as Cloud)}>
            {CLOUDS.map(([id, name]) => (
              <option key={id} value={id}>
                {name}
              </option>
            ))}
          </select>
        </Field>
        {keys[cloud] ? (
          <Field label={t("setup.model.model")}>
            <select value={config.cloud[cloud].model} onChange={(e) => use(cloud, e.target.value)}>
              <option value="">{cloud === "anthropic" ? "claude-opus-5-5" : "—"}</option>
              {models.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </Field>
        ) : (
          <Field label={t("models.key")} hint={t("models.key.hint")}>
            <div className="row">
              <input
                type="password"
                autoComplete="off"
                value={key}
                onChange={(e) => setKey(e.target.value)}
                placeholder="sk-…"
              />
              <button onClick={() => void saveKey()} disabled={key.trim().length < 8}>
                {t("common.save")}
              </button>
            </div>
          </Field>
        )}
      </div>
      {keys[cloud] ? (
        <span className="ok-text small">{t("models.key.ok")}</span>
      ) : (
        <a className="small" href={link} target="_blank" rel="noreferrer noopener">
          {t("models.key.get")}
        </a>
      )}
      {error && <p className="error-text small">{error}</p>}
    </>
  );
}

function ModelStep({
  config,
  setConfig,
  save,
}: {
  config: AppConfig;
  setConfig: SetConfig;
  save: (c: AppConfig) => Promise<boolean>;
}) {
  const [mode, setMode] = useState<"local" | "cloud">(
    config.routing.reasoning.provider === "local" ? "local" : "cloud",
  );
  const toLocal = () => {
    setMode("local");
    const route = { provider: "local" as const, fallback: "none" as const };
    setConfig({ ...config, routing: { reasoning: route, fast: route, vision: route } });
  };
  return (
    <>
      <h1>{t("setup.model.title")}</h1>
      <p className="muted">{t("setup.model.text")}</p>
      <div className="row wrap">
        <button className={mode === "local" ? "chip active" : "chip"} onClick={toLocal}>
          {t("setup.model.local")}
        </button>
        <button className={mode === "cloud" ? "chip active" : "chip"} onClick={() => setMode("cloud")}>
          {t("setup.model.cloud")}
        </button>
      </div>
      {mode === "local" ? (
        <LocalModel config={config} setConfig={setConfig} />
      ) : (
        <CloudModel config={config} setConfig={setConfig} />
      )}
      <ModelTest key={mode} config={config} save={save} />
    </>
  );
}

function CheckStep({ live }: { live: LiveState }) {
  const { info, refresh } = useSetupInfo(5000);
  const checks = info?.checks.filter((c) => c.id !== "voice") ?? [];
  return (
    <>
      <h1>{t("setup.check.title")}</h1>
      <p className="muted">{t("setup.check.text")}</p>
      {info ? (
        <ul className="check-list">
          {checks.map((check) => (
            <CheckRow key={check.id} check={check} />
          ))}
        </ul>
      ) : (
        <p className="muted">{t("setup.check.running")}</p>
      )}
      <Preview live={live} paused={false} />
      <div className="row wrap">
        <button onClick={() => void refresh()}>{t("setup.check.again")}</button>
        <button onClick={() => void api.post("/api/overlay/test")}>{t("setup.check.overlay")}</button>
      </div>
    </>
  );
}

function VoiceStep({
  config,
  setConfig,
  save,
  live,
}: {
  config: AppConfig;
  setConfig: SetConfig;
  save: (c: AppConfig) => Promise<boolean>;
  live: LiveState;
}) {
  const [devices, setDevices] = useState<Devices | null>(null);
  useEffect(() => {
    api
      .get<Devices>("/api/voice/devices")
      .then(setDevices)
      .catch(() => undefined);
  }, []);
  const voice = config.voice;
  const speaks = config.output.answers.includes("voice");
  const setSpeaks = (on: boolean) =>
    setConfig({
      ...config,
      output: {
        ...config.output,
        answers: on ? [...config.output.answers, "voice"] : config.output.answers.filter((k) => k !== "voice"),
      },
    });
  const status = live.statuses.voice;
  const hint = t("setup.voice.input.hint")
    .replace("{phrase}", voice.wake.phrases[0] ?? "")
    .replace("{key}", formatKey(voice.push_to_talk.key));
  const test = async () => {
    if (await save(config)) await api.post("/api/voice/say", { text: t("voice.tts.sample") }).catch(() => undefined);
  };

  return (
    <>
      <h1>{t("setup.voice.title")}</h1>
      <p className="muted">{t("setup.voice.text")}</p>
      <label className="check big-check">
        <input
          type="checkbox"
          checked={voice.enabled}
          onChange={(e) => setConfig({ ...config, voice: { ...voice, enabled: e.target.checked } })}
        />
        <span>
          <strong>{t("setup.voice.input")}</strong>
          <span className="field-hint block">{hint}</span>
        </span>
      </label>
      {voice.enabled && (
        <Field label={t("voice.device")}>
          <select
            value={voice.input_device}
            onChange={(e) => setConfig({ ...config, voice: { ...voice, input_device: e.target.value } })}
          >
            <option value="">{t("voice.device.default")}</option>
            {devices?.inputs.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </Field>
      )}
      <label className="check big-check">
        <input type="checkbox" checked={speaks} onChange={(e) => setSpeaks(e.target.checked)} />
        <span>
          <strong>{t("setup.voice.output")}</strong>
        </span>
      </label>
      {(voice.enabled || speaks) && (
        <div className="row wrap">
          <button onClick={() => void test()}>{t("setup.voice.test")}</button>
          {status && (
            <span className="muted small">
              <span className={`dot ${status.state === "error" ? "bad" : status.state === "ready" ? "ok" : "busy"}`} />{" "}
              {status.detail}
            </span>
          )}
        </div>
      )}
    </>
  );
}

export function Setup({ live, onDone }: { live: LiveState; onDone: () => void }) {
  const { config, setConfig, save, reload, error } = useConfig();
  const [step, setStep] = useState(0);
  const [busy, setBusy] = useState(false);
  if (!config) return null;

  const finish = async () => {
    setBusy(true);
    const done = await save({ ...config, setup_completed: true });
    setBusy(false);
    if (done) onDone();
  };
  const next = async () => {
    if (step === STEPS.length - 1) return finish();
    setBusy(true);
    const ok = await save(config);
    setBusy(false);
    if (ok) setStep(step + 1);
  };
  const props = { config, setConfig, save };

  return (
    <div className="setup">
      <header className="setup-head">
        <img src="./icon.png" alt="" width={48} height={48} />
        <div>
          <h1>{t("setup.title")}</h1>
          <p className="muted">{t("setup.subtitle")}</p>
        </div>
        <button className="link" onClick={() => void finish()}>
          {t("setup.skip")}
        </button>
      </header>
      <ol className="steps">
        {STEPS.map((label, i) => (
          <li key={label} className={i === step ? "active" : i < step ? "done" : ""}>
            <span>{i + 1}</span>
            {t(label)}
          </li>
        ))}
      </ol>
      <section className="card setup-card">
        {step === 0 && <GameStep config={config} setConfig={setConfig} reload={reload} />}
        {step === 1 && <ModelStep {...props} />}
        {step === 2 && <CheckStep live={live} />}
        {step === 3 && <VoiceStep {...props} live={live} />}
      </section>
      <div className="setup-nav">
        <button onClick={() => setStep(step - 1)} disabled={step === 0 || busy}>
          {t("setup.back")}
        </button>
        {error && (
          <span className="error-text small" role="alert">
            {error}
          </span>
        )}
        <button className="primary" onClick={() => void next()} disabled={busy}>
          {step === STEPS.length - 1 ? t("setup.finish") : t("setup.next")}
        </button>
      </div>
    </div>
  );
}
