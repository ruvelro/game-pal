import { useCallback, useEffect, useState } from "react";
import {
  api,
  formatBytes,
  isRemote,
  type AppConfig,
  type Devices,
  type Hardware,
  type LocalServer,
  type ProfileSummary,
  type WindowSummary,
} from "../api";
import { CheckRow, CommandBox, useSetupInfo } from "../components/Checks";
import { ConnectionTest, ServerKey } from "../components/ConnectionTest";
import { Downloads as EngineDownloads, useEngine } from "../components/Engine";
import { Preview } from "../components/Preview";
import { StatusDownloads } from "../components/StatusDownloads";
import { Field, formatKey } from "../components/ui";
import { LANGUAGES, t, type Key } from "../i18n";
import { useConfig, type LiveState } from "../store";

type Cloud = "openai" | "anthropic" | "openrouter";
type SetConfig = (config: AppConfig) => void;
type Save = (config: AppConfig) => Promise<boolean>;
// Dónde funciona el modelo: el motor que instala game-pal, un servidor que ya tiene el jugador o la nube.
type Mode = "managed" | "external" | "cloud";

const STEPS: Key[] = ["setup.step.game", "setup.step.model", "setup.step.check", "setup.step.voice"];
const TIERS: Array<[number, Key, Key]> = [
  [0, "setup.tier.0", "setup.tier.0.hint"],
  [1, "setup.tier.1", "setup.tier.1.hint"],
  [2, "setup.tier.2", "setup.tier.2.hint"],
  [3, "setup.tier.3", "setup.tier.3.hint"],
];
const CLOUDS: Array<[Cloud, string, string]> = [
  ["anthropic", "Anthropic (Claude)", "https://platform.claude.com/settings/keys"],
  ["openai", "OpenAI", "https://platform.openai.com/api-keys"],
  ["openrouter", "OpenRouter", "https://openrouter.ai/keys"],
];
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

/** Un servidor que ya tiene el jugador: en este equipo, en otro de su red (Ollama, LM Studio, NVIDIA PAIR…) o por https. */
function LocalModel({ config, setConfig }: { config: AppConfig; setConfig: SetConfig }) {
  const [servers, setServers] = useState<LocalServer[]>();
  const [tested, setTested] = useState<string[]>();
  const [serverKey, setServerKey] = useState("");
  const [keyed, setKeyed] = useState(false);
  const discover = useCallback(
    () =>
      api
        .get<{ servers: LocalServer[] }>("/api/providers/local/discover")
        .then((data) => setServers(data.servers))
        .catch(() => setServers([])),
    [],
  );
  const loadKeys = useCallback(
    () =>
      api
        .get<{ keys: Record<string, boolean> }>("/api/providers")
        .then((data) => setKeyed(Boolean(data.keys.server)))
        .catch(() => undefined),
    [],
  );
  useEffect(() => void discover(), [discover]);
  useEffect(() => void loadKeys(), [loadKeys]);
  // Mientras no haya ningún servidor, se sigue buscando: así se detecta Ollama en cuanto se abre.
  useEffect(() => {
    if (servers?.length !== 0) return;
    const id = window.setInterval(() => void discover(), 5000);
    return () => window.clearInterval(id);
  }, [servers, discover]);

  const provider = config.provider;
  const remote = isRemote(provider.base_url);
  const current = servers?.find((s) => s.url === provider.base_url.replace(/\/+$/, ""));
  const models = tested ?? current?.models;
  const choose = useCallback(
    (server: LocalServer, model?: string) => {
      const keep = model ?? (server.models.includes(provider.model) ? provider.model : (server.recommended ?? ""));
      setTested(undefined);
      setConfig({ ...config, provider: { ...provider, base_url: server.url, model: keep } });
    },
    [config, provider, setConfig],
  );
  // Si el servidor configurado en este equipo no responde pero hay otro, se propone ese. Una dirección
  // de otro equipo escrita por el jugador no se toca.
  useEffect(() => {
    if (servers && servers.length > 0 && !current && !remote && servers[0]) choose(servers[0]);
  }, [servers, current, remote, choose]);
  const setUrl = (base_url: string) => {
    setTested(undefined);
    setConfig({ ...config, provider: { ...provider, base_url } });
  };
  const setModel = (model: string) => setConfig({ ...config, provider: { ...provider, model } });

  return (
    <>
      {servers === undefined && <p className="muted">{t("setup.model.searching")}</p>}
      {servers && servers.length > 0 && (
        <div className="row wrap">
          <span className="muted small">{t("models.detected")}:</span>
          {servers.map((server) => (
            <button
              key={server.url}
              className={server.url === current?.url ? "chip active" : "chip"}
              onClick={() => choose(server)}
            >
              {server.name} · {server.models.length} {t("models.detect.count")}
            </button>
          ))}
        </div>
      )}
      {servers?.length === 0 && !remote && (
        <details className="notice">
          <summary>{t("setup.model.none")}</summary>
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
        </details>
      )}
      <div className="two-col tight">
        <Field label={t("models.url")} hint={t("models.url.hint")}>
          <input
            value={provider.base_url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="http://192.168.1.20:11434/v1"
            spellCheck={false}
          />
        </Field>
        <Field label={t("setup.model.model")}>
          {models && models.length > 0 ? (
            <select value={provider.model} onChange={(e) => setModel(e.target.value)}>
              <option value="">—</option>
              {models.map((m) => (
                <option key={m} value={m}>
                  {m === current?.recommended ? `${m} · ${t("models.recommended")}` : m}
                </option>
              ))}
            </select>
          ) : (
            <input
              value={provider.model}
              onChange={(e) => setModel(e.target.value)}
              placeholder={t("setup.model.model.hint")}
              spellCheck={false}
            />
          )}
        </Field>
      </div>
      {remote && <p className="notice small">{t("models.remote.privacy")}</p>}
      <details className="advanced">
        <summary>{t("models.server.key")}</summary>
        <div className="advanced-body">
          <ServerKey configured={keyed} value={serverKey} onChange={setServerKey} onSaved={() => void loadKeys()} />
        </div>
      </details>
      <ConnectionTest
        target="local"
        baseUrl={provider.base_url}
        model={provider.model}
        serverKey={serverKey}
        onModels={(list) => {
          setTested(list);
          if (!provider.model && list[0]) setModel(list[0]);
        }}
      />
    </>
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
      {keys[cloud] && <ConnectionTest target={cloud} model={config.cloud[cloud].model} />}
    </>
  );
}

/** El motor llama.cpp de game-pal: nivel según el equipo, descarga verificada y listo. */
function ManagedModel({
  config,
  save,
  reload,
  live,
}: {
  config: AppConfig;
  save: Save;
  reload: () => Promise<unknown>;
  live: LiveState;
}) {
  const { engine, reload: reloadEngine, error, setError } = useEngine(live);
  const [hardware, setHardware] = useState<Hardware | null>(null);
  const [tier, setTier] = useState<number | null>(config.hardware_tier);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api
      .get<Hardware>("/api/hardware")
      .then((data) => {
        setHardware(data);
        setTier((current) => current ?? data.tier);
      })
      .catch(() => undefined);
  }, []);
  if (!engine || tier === null) return <p className="muted">{t("setup.pc.detecting")}</p>;

  const idsFor = (level: number) => {
    const preset = engine.tiers.find((p) => p.tier === level);
    return [...new Set([preset?.reasoning, preset?.fast, preset?.vision].filter((x): x is string => Boolean(x)))];
  };
  // Lo que falta por descargar para un nivel: sus modelos y el motor, si no están ya.
  const sizeOf = (level: number) =>
    engine.models.filter((m) => idsFor(level).includes(m.id) && !m.installed).reduce((sum, m) => sum + m.size, 0) +
    (engine.installed ? 0 : engine.runtime_size);
  const missing = sizeOf(tier);
  const applied = config.provider.kind === "managed" && config.hardware_tier === tier;
  const gpu = hardware?.gpus.map((g) => (g.vram_gb ? `${g.name} (${g.vram_gb} GB)` : g.name)).join(", ");

  const install = async () => {
    setError(null);
    setBusy(true);
    try {
      // Primero lo pendiente de guardar; luego el nivel (modelos, voz y enrutado) y la descarga.
      if (!(await save(config))) return;
      await api.post<AppConfig>("/api/setup/tier", { tier, engine: "managed" });
      await reload();
      if (missing) await api.post("/api/engine/install", { models: idsFor(tier) });
      reloadEngine();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      {hardware && (
        <p className="small muted">
          {hardware.cpu} · {hardware.ram_gb} GB · {gpu || t("setup.pc.nogpu")}
        </p>
      )}
      <div className="tier-grid" role="radiogroup" aria-label={t("setup.tier.title")}>
        {TIERS.map(([level, label, hint]) => (
          <button
            key={level}
            role="radio"
            aria-checked={tier === level}
            className={`tier ${tier === level ? "selected" : ""}`}
            onClick={() => setTier(level)}
          >
            <strong>{t(label)}</strong>
            <span className="small muted">{t(hint)}</span>
            <span className="small">
              {sizeOf(level) ? `${t("setup.tier.download")} ${formatBytes(sizeOf(level))}` : t("setup.tier.installed")}
            </span>
            {hardware?.tier === level && <span className="chip-static ok small-chip">{t("setup.tier.recommended")}</span>}
          </button>
        ))}
      </div>
      {hardware && <p className="small muted">{hardware.reason}</p>}
      {applied && !missing ? (
        <p className="ok-text">{t("setup.models.ready")}</p>
      ) : (
        <div className="check-body">
          <button className="primary" disabled={busy || engine.busy} onClick={() => void install()}>
            {missing ? `${t("setup.models.download")} (${formatBytes(missing)})` : t("setup.model.use")}
          </button>
          {missing > 0 && <span className="small muted">{t("setup.models.hint")}</span>}
        </div>
      )}
      <EngineDownloads live={live} engine={engine} />
      {error && <p className="error-text small">{error}</p>}
      {applied && !missing && <ConnectionTest target="local" />}
    </>
  );
}

function ModelStep({
  config,
  setConfig,
  save,
  reload,
  live,
}: {
  config: AppConfig;
  setConfig: SetConfig;
  save: Save;
  reload: () => Promise<unknown>;
  live: LiveState;
}) {
  const initial: Mode | null =
    config.routing.reasoning.provider !== "local"
      ? "cloud"
      : config.provider.kind === "managed"
        ? "managed"
        : config.provider.model
          ? "external"
          : null;
  const [mode, setMode] = useState<Mode | null>(initial);
  // Sin nada elegido aún: si ya hay un servidor en marcha se usa ese; si no, se propone instalar el motor.
  useEffect(() => {
    if (mode !== null) return;
    api
      .get<{ servers: LocalServer[] }>("/api/providers/local/discover")
      .then((data) => setMode(data.servers.length ? "external" : "managed"))
      .catch(() => setMode("managed"));
  }, [mode]);

  const local = { provider: "local" as const, fallback: "none" as const };
  const toExternal = () => {
    setMode("external");
    const provider =
      config.provider.kind === "managed"
        ? { ...config.provider, kind: "local-openai" as const, model: "", fast_model: "", vision_model: "" }
        : config.provider;
    setConfig({ ...config, provider, routing: { reasoning: local, fast: local, vision: local } });
  };
  const OPTIONS: Array<[Mode, Key, () => void]> = [
    ["managed", "setup.model.managed", () => setMode("managed")],
    ["external", "setup.model.external", toExternal],
    ["cloud", "setup.model.cloud", () => setMode("cloud")],
  ];
  const hint: Record<Mode, Key> = {
    managed: "setup.engine.managed.hint",
    external: "setup.engine.external.hint",
    cloud: "setup.engine.cloud.hint",
  };

  return (
    <>
      <h1>{t("setup.model.title")}</h1>
      <p className="muted">{t("setup.model.text")}</p>
      <div className="row wrap" role="radiogroup" aria-label={t("setup.model.title")}>
        {OPTIONS.map(([id, label, choose]) => (
          <button key={id} role="radio" aria-checked={mode === id} className={mode === id ? "chip active" : "chip"} onClick={choose}>
            {t(label)}
          </button>
        ))}
      </div>
      {mode && <p className="small muted">{t(hint[mode])}</p>}
      {mode === "managed" && <ManagedModel config={config} save={save} reload={reload} live={live} />}
      {mode === "external" && <LocalModel config={config} setConfig={setConfig} />}
      {mode === "cloud" && <CloudModel config={config} setConfig={setConfig} />}
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
  const [mic, setMic] = useState<{ peak_db: number; heard: boolean } | null>(null);
  const [listening, setListening] = useState(false);
  const [sound, setSound] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const testMic = async () => {
    setError(null);
    setListening(true);
    try {
      if (await save(config)) setMic(await api.post<{ peak_db: number; heard: boolean }>("/api/voice/mic-test"));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setListening(false);
    }
  };
  const testSpeaker = async () => {
    setError(null);
    try {
      if (!(await save(config))) return;
      const result = await api.post<{ mode: string }>("/api/voice/speaker-test");
      setSound(t(result.mode === "voice" ? "setup.voice.spoke" : "setup.voice.beeped"));
    } catch (e) {
      setError((e as Error).message);
    }
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
      {voice.enabled && (
        <div className="row wrap">
          <button onClick={() => void testMic()} disabled={listening}>
            {listening ? t("setup.voice.listening") : t("setup.voice.mic")}
          </button>
          {mic && (
            <span className={mic.heard ? "ok-text" : "error-text"}>
              {mic.heard ? t("setup.voice.heard") : t("setup.voice.silent")} ({mic.peak_db} dBFS)
            </span>
          )}
        </div>
      )}
      <div className="row wrap">
        <button onClick={() => void testSpeaker()}>{t("setup.voice.speaker")}</button>
        {sound && <span className="small muted">{sound}</span>}
      </div>
      {error && <p className="error-text small">{error}</p>}
      {(voice.enabled || speaks) && (
        <div className="row wrap">
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
      <StatusDownloads live={live} />
      <section className="card setup-card">
        {step === 0 && <GameStep config={config} setConfig={setConfig} reload={reload} />}
        {step === 1 && <ModelStep {...props} reload={reload} live={live} />}
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
