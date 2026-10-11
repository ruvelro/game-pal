import { useEffect, useRef, useState } from "react";
import { api, type AppConfig, type Devices } from "../api";
import { Advanced, Card, Field } from "../components/ui";
import { LANGUAGES, t } from "../i18n";

const WHISPER_MODELS = ["tiny", "base", "small", "medium", "large-v3-turbo"];
const GAMEPAD_BUTTONS = [
  "back",
  "start",
  "guide",
  "leftstick",
  "rightstick",
  "leftshoulder",
  "rightshoulder",
  "a",
  "b",
  "x",
  "y",
  "dpup",
  "dpdown",
  "misc1",
  "touchpad",
];

const list = (text: string) =>
  text
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);

/** Convierte una pulsación del navegador a la sintaxis de atajos de pynput: «<ctrl>+<alt>+p». */
function toCombo(event: KeyboardEvent): string | null {
  const modifiers = ["Control", "Alt", "Shift", "Meta"];
  if (modifiers.includes(event.key)) return null;
  const parts: string[] = [];
  if (event.ctrlKey) parts.push("<ctrl>");
  if (event.altKey) parts.push("<alt>");
  if (event.shiftKey) parts.push("<shift>");
  if (event.metaKey) parts.push("<cmd>");
  const code = event.code;
  let key: string;
  if (/^F\d{1,2}$/.test(event.key)) key = `<${event.key.toLowerCase()}>`;
  else if (code === "Space") key = "<space>";
  else if (code.startsWith("Key")) key = code.slice(3).toLowerCase();
  else if (code.startsWith("Digit")) key = code.slice(5);
  else if (event.key.length === 1) key = event.key.toLowerCase();
  else key = `<${event.key.toLowerCase()}>`;
  parts.push(key);
  return parts.join("+");
}

function ComboInput({ value, onChange }: { value: string; onChange: (combo: string) => void }) {
  const [recording, setRecording] = useState(false);
  useEffect(() => {
    if (!recording) return;
    const handler = (event: KeyboardEvent) => {
      event.preventDefault();
      const combo = toCombo(event);
      if (combo) {
        onChange(combo);
        setRecording(false);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [recording, onChange]);
  return (
    <div className="row">
      <input value={value} onChange={(e) => onChange(e.target.value)} spellCheck={false} />
      <button onClick={() => setRecording(true)} className={recording ? "primary" : ""}>
        {recording ? t("voice.ptt.recording") : t("voice.ptt.record")}
      </button>
    </div>
  );
}

/** Cómo pedirle consejo hablando y cómo suena su voz. */
export function VoiceSections({
  config,
  setConfig,
  save,
}: {
  config: AppConfig;
  setConfig: (c: AppConfig) => void;
  save: (c: AppConfig) => Promise<boolean>;
}) {
  const [devices, setDevices] = useState<Devices | null>(null);
  const [detecting, setDetecting] = useState(false);
  const baseline = useRef<string | null>(null);
  const voice = config.voice;
  const update = (patch: Partial<AppConfig["voice"]>) => setConfig({ ...config, voice: { ...voice, ...patch } });

  useEffect(() => {
    api
      .get<Devices>("/api/voice/devices")
      .then(setDevices)
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!detecting) return;
    const id = window.setInterval(async () => {
      const next = await api.get<Devices>("/api/voice/devices").catch(() => null);
      if (!next) return;
      setDevices(next);
      if (next.last_button && next.last_button !== baseline.current) {
        update({ push_to_talk: { ...voice.push_to_talk, gamepad_button: next.last_button } });
        setDetecting(false);
      }
    }, 400);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detecting]);

  const speaks = config.output.answers.includes("voice");
  const testVoice = async () => {
    if (await save(config)) await api.post("/api/voice/say", { text: t("voice.tts.sample") }).catch(() => undefined);
  };

  return (
    <div className="two-col">
      <Card title={t("voice.ask")}>
        <label className="check big-check">
          <input type="checkbox" checked={voice.enabled} onChange={(e) => update({ enabled: e.target.checked })} />
          <span>
            <strong>{t("voice.enabled")}</strong>
            <span className="field-hint block">{t("voice.enabled.hint")}</span>
          </span>
        </label>
        {voice.enabled && (
          <>
            <Field label={t("voice.device")}>
              <select value={voice.input_device} onChange={(e) => update({ input_device: e.target.value })}>
                <option value="">{t("voice.device.default")}</option>
                {devices?.inputs.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            </Field>
            <label className="check">
              <input
                type="checkbox"
                checked={voice.wake.enabled}
                onChange={(e) => update({ wake: { ...voice.wake, enabled: e.target.checked } })}
              />
              {t("voice.wake.enabled")}
            </label>
            {voice.wake.enabled && (
              <Field label={t("voice.wake.phrases")} hint={t("voice.wake.hint")}>
                <input
                  defaultValue={voice.wake.phrases.join(", ")}
                  onBlur={(e) => update({ wake: { ...voice.wake, phrases: list(e.target.value) } })}
                />
              </Field>
            )}
            <label className="check">
              <input
                type="checkbox"
                checked={voice.push_to_talk.enabled}
                onChange={(e) => update({ push_to_talk: { ...voice.push_to_talk, enabled: e.target.checked } })}
              />
              {t("voice.ptt.enabled")}
            </label>
            {voice.push_to_talk.enabled && (
              <>
                <Field label={t("voice.ptt.key")}>
                  <ComboInput
                    value={voice.push_to_talk.key}
                    onChange={(key) => update({ push_to_talk: { ...voice.push_to_talk, key } })}
                  />
                </Field>
                <Field label={t("voice.ptt.gamepad")}>
                  <div className="row">
                    <select
                      value={voice.push_to_talk.gamepad_button}
                      onChange={(e) =>
                        update({ push_to_talk: { ...voice.push_to_talk, gamepad_button: e.target.value } })
                      }
                    >
                      <option value="">{t("voice.ptt.gamepad.none")}</option>
                      {GAMEPAD_BUTTONS.map((b) => (
                        <option key={b} value={b}>
                          {b}
                        </option>
                      ))}
                    </select>
                    <button
                      onClick={() => {
                        baseline.current = devices?.last_button ?? null;
                        setDetecting(true);
                      }}
                      disabled={!devices?.gamepads.length}
                    >
                      {detecting ? t("voice.ptt.detecting") : t("voice.ptt.detect")}
                    </button>
                  </div>
                  {devices && (
                    <span className="field-hint">
                      {t("voice.ptt.gamepads")}: {devices.gamepads.join(", ") || "—"}
                      {devices.gamepad_error ? ` · ${devices.gamepad_error}` : ""}
                    </span>
                  )}
                </Field>
              </>
            )}
            <Advanced>
              <Field label={t("voice.ptt.mode")}>
                <select
                  value={voice.push_to_talk.mode}
                  onChange={(e) =>
                    update({ push_to_talk: { ...voice.push_to_talk, mode: e.target.value as "hold" | "toggle" } })
                  }
                >
                  <option value="hold">{t("voice.ptt.hold")}</option>
                  <option value="toggle">{t("voice.ptt.toggle")}</option>
                </select>
              </Field>
              <Field label={t("voice.language")}>
                <select value={voice.language} onChange={(e) => update({ language: e.target.value })}>
                  <option value="">{t("voice.language.same")}</option>
                  {LANGUAGES.map(([code, name]) => (
                    <option key={code} value={code}>
                      {name}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label={t("voice.stop")}>
                <input
                  defaultValue={voice.stop_phrases.join(", ")}
                  onBlur={(e) => update({ stop_phrases: list(e.target.value) })}
                />
              </Field>
              <Field label={t("voice.stt.model")} hint={t("voice.stt.hint")}>
                <select
                  value={voice.stt.model}
                  onChange={(e) => update({ stt: { ...voice.stt, model: e.target.value } })}
                >
                  {WHISPER_MODELS.map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                </select>
              </Field>
              <Field
                label={`${t("voice.vad")}: ${Math.round(voice.vad_threshold * 100)} %`}
                hint={t("voice.vad.hint")}
              >
                <input
                  type="range"
                  min={0.2}
                  max={0.9}
                  step={0.05}
                  value={voice.vad_threshold}
                  onChange={(e) => update({ vad_threshold: Number(e.target.value) })}
                />
              </Field>
            </Advanced>
          </>
        )}
      </Card>

      <Card title={t("voice.tts")}>
        <p className="muted small">{speaks ? t("voice.tts.on") : t("voice.tts.off")}</p>
        {voice.tts.backend === "piper" && (
          <Field label={t("voice.tts.voice")}>
            <select value={voice.tts.voice} onChange={(e) => update({ tts: { ...voice.tts, voice: e.target.value } })}>
              <option value="">{t("voice.tts.auto")}</option>
              {Object.entries(devices?.voices ?? {}).map(([lang, id]) => (
                <option key={id} value={id}>
                  {lang} · {id}
                </option>
              ))}
            </select>
          </Field>
        )}
        <div>
          <button onClick={() => void testVoice()} disabled={voice.tts.backend === "none"}>
            {t("voice.tts.test")}
          </button>
        </div>
        <Advanced>
          <Field label={t("voice.tts.backend")}>
            <select
              value={voice.tts.backend}
              onChange={(e) =>
                update({ tts: { ...voice.tts, backend: e.target.value as AppConfig["voice"]["tts"]["backend"] } })
              }
            >
              <option value="piper">{t("voice.tts.piper")}</option>
              <option value="system">{t("voice.tts.system")}</option>
              <option value="none">{t("voice.tts.none")}</option>
            </select>
          </Field>
          <Field label={`${t("voice.tts.volume")}: ${Math.round(voice.tts.volume * 100)} %`}>
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={voice.tts.volume}
              onChange={(e) => update({ tts: { ...voice.tts, volume: Number(e.target.value) } })}
            />
          </Field>
          <Field label={`${t("voice.tts.rate")}: ${voice.tts.rate.toFixed(2)}×`}>
            <input
              type="range"
              min={0.6}
              max={1.6}
              step={0.05}
              value={voice.tts.rate}
              onChange={(e) => update({ tts: { ...voice.tts, rate: Number(e.target.value) } })}
            />
          </Field>
        </Advanced>
      </Card>
    </div>
  );
}
