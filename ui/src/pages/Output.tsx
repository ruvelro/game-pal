import { api, type AppConfig, type OutputKind } from "../api";
import { Card, Field, PageHeader, SaveBar } from "../components/ui";
import { t, type Key } from "../i18n";
import { useConfig } from "../store";

const KINDS: Array<[OutputKind, Key]> = [
  ["overlay", "output.overlay"],
  ["text", "output.text"],
  ["voice", "output.voice"],
];
const CORNERS: AppConfig["output"]["overlay"]["corner"][] = ["top-left", "top-right", "bottom-left", "bottom-right"];

export function Output() {
  const { config, setConfig, save, error, saved } = useConfig();
  if (!config) return null;
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

  return (
    <>
      <PageHeader title={t("output.title")} subtitle={t("output.subtitle")} />
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
      </Card>

      <div className="two-col">
        <Card title={t("output.overlay.title")}>
          <label className="check">
            <input type="checkbox" checked={output.overlay.enabled} onChange={(e) => overlay({ enabled: e.target.checked })} />
            {t("output.overlay.enabled")}
          </label>
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
          <button onClick={() => void preview()} disabled={!output.overlay.enabled}>
            {t("output.overlay.test")}
          </button>
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
        </Card>
      </div>
      <SaveBar onSave={() => void save(config)} saved={saved} error={error} />
    </>
  );
}
