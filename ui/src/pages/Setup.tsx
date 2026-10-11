import { useEffect, useState } from "react";
import { api, formatBytes, type AppConfig, type Hardware, type ProfileSummary } from "../api";
import { Downloads, useEngine } from "../components/Engine";
import { Card } from "../components/ui";
import { t, type Key } from "../i18n";
import type { LiveState } from "../store";
import { Preview } from "./Home";

type Engine = "managed" | "external" | "cloud";

const STEPS: Key[] = ["setup.step.pc", "setup.step.models", "setup.step.voice", "setup.step.game"];
const TIERS: Array<[number, Key, Key]> = [
  [0, "setup.tier.0", "setup.tier.0.hint"],
  [1, "setup.tier.1", "setup.tier.1.hint"],
  [2, "setup.tier.2", "setup.tier.2.hint"],
  [3, "setup.tier.3", "setup.tier.3.hint"],
];

function StepPc({
  hardware,
  tier,
  setTier,
  engine,
  setEngine,
  live,
}: {
  hardware: Hardware | null;
  tier: number;
  setTier: (tier: number) => void;
  engine: Engine;
  setEngine: (engine: Engine) => void;
  live: LiveState;
}) {
  const { engine: status } = useEngine(live);
  // Lo que falta por descargar para ese nivel: los modelos y el motor que no estén ya instalados.
  const sizeOf = (level: number) => {
    const preset = status?.tiers.find((p) => p.tier === level);
    if (!preset || !status) return 0;
    const ids = new Set([preset.reasoning, preset.fast, preset.vision].filter(Boolean));
    const models = status.models.filter((m) => ids.has(m.id) && !m.installed).reduce((sum, m) => sum + m.size, 0);
    return models + (status.installed ? 0 : status.runtime_size);
  };
  const gpu = hardware?.gpus.map((g) => (g.vram_gb ? `${g.name} (${g.vram_gb} GB)` : g.name)).join(", ");

  return (
    <>
      <Card title={t("setup.pc.title")}>
        {hardware ? (
          <dl className="hw-facts">
            <dt>{t("setup.pc.cpu")}</dt>
            <dd>
              {hardware.cpu} · {hardware.cores} {t("setup.pc.cores")}
            </dd>
            <dt>{t("setup.pc.ram")}</dt>
            <dd>{hardware.ram_gb} GB</dd>
            <dt>{t("setup.pc.gpu")}</dt>
            <dd>{gpu || t("setup.pc.nogpu")}</dd>
            <dt>{t("setup.pc.models")}</dt>
            <dd>{hardware.model_memory_gb ? `${hardware.model_memory_gb} GB` : t("setup.pc.cpuonly")}</dd>
          </dl>
        ) : (
          <p className="muted">{t("setup.pc.detecting")}</p>
        )}
      </Card>
      <Card title={t("setup.tier.title")}>
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
                {status && (sizeOf(level) ? `${t("setup.tier.download")} ${formatBytes(sizeOf(level))}` : t("setup.tier.installed"))}
              </span>
              {hardware?.tier === level && <span className="chip-static ok small-chip">{t("setup.tier.recommended")}</span>}
            </button>
          ))}
        </div>
        {hardware && <p className="small muted">{hardware.reason}</p>}
      </Card>
      <Card title={t("setup.engine.title")}>
        {(["managed", "external", "cloud"] as Engine[]).map((option) => (
          <label key={option} className="check option">
            <input type="radio" name="engine" checked={engine === option} onChange={() => setEngine(option)} />
            <span>
              <strong>{t(`setup.engine.${option}` as Key)}</strong>
              <span className="small muted block">{t(`setup.engine.${option}.hint` as Key)}</span>
            </span>
          </label>
        ))}
      </Card>
    </>
  );
}

function StepModels({ engine, tier, live }: { engine: Engine; tier: number; live: LiveState }) {
  const { engine: status, reload, error, setError } = useEngine(live);
  if (engine !== "managed") {
    return (
      <Card title={t("setup.models.title")}>
        <p>{t(engine === "external" ? "setup.models.external" : "setup.models.cloud")}</p>
      </Card>
    );
  }
  if (!status) return null;
  const preset = status.tiers.find((p) => p.tier === tier);
  const ids = [...new Set([preset?.reasoning, preset?.fast, preset?.vision].filter((x): x is string => Boolean(x)))];
  const models = status.models.filter((m) => ids.includes(m.id));
  const missing = models.filter((m) => !m.installed);
  const total = missing.reduce((sum, m) => sum + m.size, 0) + (status.installed ? 0 : status.runtime_size);
  const ready = status.installed && missing.length === 0;

  const start = async () => {
    setError(null);
    try {
      await api.post("/api/engine/install", { models: ids });
      reload();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <Card title={t("setup.models.title")}>
      <ul className="plain">
        <li>
          llama.cpp {status.tag} · {status.variant} {status.installed ? "✓" : `· ${formatBytes(status.runtime_size)}`}
        </li>
        {models.map((m) => (
          <li key={m.id}>
            {m.name} · {formatBytes(m.size)} {m.installed ? "✓" : ""}
          </li>
        ))}
      </ul>
      {ready ? (
        <p className="ok-text">{t("setup.models.ready")}</p>
      ) : (
        <>
          <p className="small muted">{t("setup.models.hint")}</p>
          <button className="primary" disabled={status.busy} onClick={() => void start()}>
            {t("setup.models.download")} ({formatBytes(total)})
          </button>
        </>
      )}
      <Downloads live={live} engine={status} />
      {error && <p className="error-text small">{error}</p>}
    </Card>
  );
}

function StepVoice({ config, setConfig }: { config: AppConfig; setConfig: (c: AppConfig) => void }) {
  const [mic, setMic] = useState<{ peak_db: number; heard: boolean } | null>(null);
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sound, setSound] = useState<string | null>(null);

  const testMic = async () => {
    setError(null);
    setTesting(true);
    try {
      setMic(await api.post<{ peak_db: number; heard: boolean }>("/api/voice/mic-test"));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setTesting(false);
    }
  };
  const testSpeaker = async () => {
    setError(null);
    try {
      const result = await api.post<{ mode: string }>("/api/voice/speaker-test");
      setSound(t(result.mode === "voice" ? "setup.voice.spoke" : "setup.voice.beeped"));
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <Card title={t("setup.voice.title")}>
      <p className="small muted">{t("setup.voice.hint")}</p>
      <div className="row wrap">
        <button onClick={() => void testMic()} disabled={testing}>
          {testing ? t("setup.voice.listening") : t("setup.voice.mic")}
        </button>
        {mic && (
          <span className={mic.heard ? "ok-text" : "error-text"}>
            {mic.heard ? t("setup.voice.heard") : t("setup.voice.silent")} ({mic.peak_db} dBFS)
          </span>
        )}
      </div>
      <div className="row wrap">
        <button onClick={() => void testSpeaker()}>{t("setup.voice.speaker")}</button>
        {sound && <span className="small muted">{sound}</span>}
      </div>
      <label className="check">
        <input
          type="checkbox"
          checked={config.voice.enabled}
          onChange={(e) => setConfig({ ...config, voice: { ...config.voice, enabled: e.target.checked } })}
        />
        {t("setup.voice.enable")}
      </label>
      {error && <p className="error-text small">{error}</p>}
    </Card>
  );
}

function StepGame({
  config,
  setConfig,
  live,
}: {
  config: AppConfig;
  setConfig: (c: AppConfig) => void;
  live: LiveState;
}) {
  const [profiles, setProfiles] = useState<ProfileSummary[]>([]);
  const [overlay, setOverlay] = useState(false);
  useEffect(() => {
    api
      .get<{ profiles: ProfileSummary[] }>("/api/profiles")
      .then((data) => setProfiles(data.profiles))
      .catch(() => undefined);
  }, []);

  return (
    <Card title={t("setup.game.title")}>
      <label className="field">
        <span className="field-label">{t("game.profile")}</span>
        <select value={config.profile} onChange={(e) => setConfig({ ...config, profile: e.target.value })}>
          {profiles.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </label>
      <p className="small muted">{t("setup.game.hint")}</p>
      <Preview live={live} paused={false} />
      <div className="row wrap">
        <button
          onClick={() =>
            void api.post("/api/overlay/test").then(() => {
              setOverlay(true);
            })
          }
        >
          {t("setup.game.overlay")}
        </button>
        {overlay && <span className="small muted">{t("setup.game.overlay.hint")}</span>}
      </div>
    </Card>
  );
}

export function Setup({ live, onDone }: { live: LiveState; onDone: () => void }) {
  const [step, setStep] = useState(0);
  const [hardware, setHardware] = useState<Hardware | null>(null);
  const [tier, setTier] = useState(1);
  const [engine, setEngine] = useState<Engine>("managed");
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .get<Hardware>("/api/hardware")
      .then((data) => {
        setHardware(data);
        setTier(data.tier);
      })
      .catch((e: Error) => setError(e.message));
    api
      .get<AppConfig>("/api/config")
      .then(setConfig)
      .catch((e: Error) => setError(e.message));
  }, []);

  const guard = async (action: () => Promise<void>) => {
    setError(null);
    setBusy(true);
    try {
      await action();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const next = () =>
    void guard(async () => {
      if (step === 0) {
        setConfig(await api.post<AppConfig>("/api/setup/tier", { tier, engine }));
      } else if ((step === 2 || step === 3) && config) {
        setConfig(await api.put<AppConfig>("/api/config", config));
      }
      if (step === STEPS.length - 1) {
        await api.post("/api/setup/done", { done: true });
        onDone();
        return;
      }
      setStep(step + 1);
    });

  const skip = () =>
    void guard(async () => {
      await api.post("/api/setup/done", { done: true });
      onDone();
    });

  return (
    <div className="setup">
      <header className="setup-head">
        <img src="./icon.png" alt="" width={48} height={48} />
        <div>
          <h1>{t("setup.title")}</h1>
          <p className="muted">{t("setup.subtitle")}</p>
        </div>
        <button className="link" onClick={skip} disabled={busy}>
          {t("setup.skip")}
        </button>
      </header>
      <ol className="steps" aria-label={t("setup.title")}>
        {STEPS.map((label, index) => (
          <li key={label} className={index === step ? "current" : index < step ? "done" : ""} aria-current={index === step}>
            <span>{index + 1}</span> {t(label)}
          </li>
        ))}
      </ol>
      {step === 0 && (
        <StepPc hardware={hardware} tier={tier} setTier={setTier} engine={engine} setEngine={setEngine} live={live} />
      )}
      {step === 1 && <StepModels engine={engine} tier={tier} live={live} />}
      {step === 2 && config && <StepVoice config={config} setConfig={setConfig} />}
      {step === 3 && config && <StepGame config={config} setConfig={setConfig} live={live} />}
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      <footer className="setup-foot">
        <button onClick={() => setStep(Math.max(0, step - 1))} disabled={step === 0 || busy}>
          {t("setup.back")}
        </button>
        <button className="primary" onClick={next} disabled={busy}>
          {step === STEPS.length - 1 ? t("setup.finish") : t("setup.next")}
        </button>
      </footer>
    </div>
  );
}
