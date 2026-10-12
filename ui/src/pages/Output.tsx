import { api, type AppConfig, type OutputKind } from "../api";
import { ComboInput } from "../components/ComboInput";
import { Advanced, Card, Field, formatKey } from "../components/ui";
import { t, type Key } from "../i18n";

const KINDS: Array<[OutputKind, Key]> = [
  ["overlay", "output.overlay"],
  ["text", "output.text"],
  ["voice", "output.voice"],
];
const CORNERS: AppConfig["output"]["overlay"]["corner"][] = ["top-left", "top-right", "bottom-left", "bottom-right"];

/** Cómo llegan los consejos (overlay, texto, voz) y los avisos por su cuenta. */
export function OutputSections({
  config,
  setConfig,
  save,
}: {
  config: AppConfig;
  setConfig: (c: AppConfig) => void;
  save: (c: AppConfig) => Promise<boolean>;
}) {
  const output = config.output;
  const overlay = (patch: Partial<AppConfig["output"]["overlay"]>) =>
    setConfig({ ...config, output: { ...output, overlay: { ...output.overlay, ...patch } } });
  const proactive = (patch: Partial<AppConfig["output"]["proactive"]>) =>
    setConfig({ ...config, output: { ...output, proactive: { ...output.proactive, ...patch } } });
  const toggleAnswer = (kind: OutputKind, on: boolean) =>
    setConfig({
      ...config,
      output: { ...output, answers: on ? [...output.answers, kind] : output.answers.filter((k) => k !== kind) },
    });
  const preview = async () => {
    if (await save(config)) window.setTimeout(() => void api.post("/api/overlay/test"), 1200);
  };
  const openChat = async () => {
    if (await save(config)) window.setTimeout(() => void api.post("/api/overlay/chat", { open: true }), 1200);
  };

  return (
    <div className="two-col">
      <Card title={t("output.answers")}>
        <div className="row wrap">
          {KINDS.map(([kind, label]) => (
            <label className="check" key={kind}>
              <input
                type="checkbox"
                checked={output.answers.includes(kind)}
                onChange={(e) => toggleAnswer(kind, e.target.checked)}
              />
              {t(label)}
            </label>
          ))}
        </div>
        <label className="check">
          <input type="checkbox" checked={output.overlay.enabled} onChange={(e) => overlay({ enabled: e.target.checked })} />
          {t("output.overlay.enabled")}
        </label>
        {output.overlay.enabled && (
          <>
            <Field label={t("output.overlay.corner")}>
              <div className="corners">
                {CORNERS.map((corner) => (
                  <button
                    key={corner}
                    className={output.overlay.corner === corner ? "corner active" : "corner"}
                    onClick={() => overlay({ corner })}
                    aria-label={t(`output.corner.${corner}` as Key)}
                    title={t(`output.corner.${corner}` as Key)}
                  />
                ))}
              </div>
            </Field>
            <div>
              <button onClick={() => void preview()}>{t("output.overlay.test")}</button>
            </div>
            <Field
              label={t("output.chat.key")}
              hint={
                output.overlay.chat_key
                  ? t("output.chat.key.hint").replace("{key}", formatKey(output.overlay.chat_key))
                  : t("output.chat.key.none")
              }
            >
              <ComboInput value={output.overlay.chat_key} onChange={(chat_key) => overlay({ chat_key })} />
            </Field>
            <div>
              <button onClick={() => void openChat()}>{t("output.chat.test")}</button>
            </div>
            <Advanced>
              <Field label={`${t("output.overlay.width")}: ${output.overlay.width} px`}>
                <input
                  type="range"
                  min={260}
                  max={720}
                  step={20}
                  value={output.overlay.width}
                  onChange={(e) => overlay({ width: Number(e.target.value) })}
                />
              </Field>
              <Field label={`${t("output.overlay.opacity")}: ${Math.round(output.overlay.opacity * 100)} %`}>
                <input
                  type="range"
                  min={0.4}
                  max={1}
                  step={0.02}
                  value={output.overlay.opacity}
                  onChange={(e) => overlay({ opacity: Number(e.target.value) })}
                />
              </Field>
              <Field label={`${t("output.overlay.font")}: ${output.overlay.font_scale.toFixed(1)}×`}>
                <input
                  type="range"
                  min={0.8}
                  max={1.8}
                  step={0.1}
                  value={output.overlay.font_scale}
                  onChange={(e) => overlay({ font_scale: Number(e.target.value) })}
                />
              </Field>
              <Field label={`${t("output.overlay.linger")}: ${output.overlay.linger_seconds}`}>
                <input
                  type="range"
                  min={3}
                  max={60}
                  step={1}
                  value={output.overlay.linger_seconds}
                  onChange={(e) => overlay({ linger_seconds: Number(e.target.value) })}
                />
              </Field>
            </Advanced>
          </>
        )}
      </Card>

      <Card title={t("output.proactive")}>
        <label className="check">
          <input
            type="checkbox"
            checked={output.proactive.enabled}
            onChange={(e) => proactive({ enabled: e.target.checked })}
          />
          {t("output.proactive.enabled")}
        </label>
        <Advanced>
          <Field label={`${t("output.proactive.interval")}: ${output.proactive.min_interval_seconds}`}>
            <input
              type="range"
              min={0}
              max={120}
              step={1}
              value={output.proactive.min_interval_seconds}
              onChange={(e) => proactive({ min_interval_seconds: Number(e.target.value) })}
            />
          </Field>
          <Field label={t("output.proactive.key")}>
            <input
              value={output.proactive.mute_key}
              onChange={(e) => proactive({ mute_key: e.target.value })}
              spellCheck={false}
            />
          </Field>
        </Advanced>
      </Card>
    </div>
  );
}
