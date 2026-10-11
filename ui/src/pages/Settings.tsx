import { useState } from "react";
import { api, type AppConfig } from "../api";
import { Card, Field, PageHeader, SaveStatus } from "../components/ui";
import { t } from "../i18n";
import { useConfig } from "../store";

function Lifecycle({ onSetup }: { onSetup: () => void }) {
  const [closed, setClosed] = useState(false);
  if (closed) return <p className="muted">{t("settings.closed")}</p>;
  return (
    <Card title={t("settings.app")}>
      <div className="row wrap">
        <button onClick={() => void api.post("/api/setup/done", { done: false }).then(onSetup)}>{t("setup.reopen")}</button>
        <button
          className="danger"
          onClick={() => {
            if (window.confirm(t("settings.quit.confirm"))) void api.post("/api/shutdown").then(() => setClosed(true));
          }}
        >
          {t("settings.quit")}
        </button>
      </div>
    </Card>
  );
}

export function Settings({ onSetup }: { onSetup: () => void }) {
  const { config, setConfig, error, saved, saving } = useConfig();
  if (!config) return null;
  const capture = (patch: Partial<AppConfig["capture"]>) =>
    setConfig({ ...config, capture: { ...config.capture, ...patch } });
  const ocr = (patch: Partial<AppConfig["ocr"]>) => setConfig({ ...config, ocr: { ...config.ocr, ...patch } });

  return (
    <>
      <PageHeader title={t("settings.title")} subtitle={t("settings.subtitle")} />
      <Card>
        <div className="two-col tight">
          <Field label={t("settings.ocr")}>
            <select
              value={config.ocr.backend}
              onChange={(e) => ocr({ backend: e.target.value as AppConfig["ocr"]["backend"] })}
            >
              <option value="rapidocr">{t("settings.ocr.rapidocr")}</option>
              <option value="apple-vision">{t("settings.ocr.vision")}</option>
              <option value="windows-ocr">{t("settings.ocr.windows")}</option>
              <option value="none">{t("settings.ocr.none")}</option>
            </select>
          </Field>
          <Field label={`${t("settings.confidence")}: ${Math.round(config.ocr.min_confidence * 100)} %`}>
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={config.ocr.min_confidence}
              onChange={(e) => ocr({ min_confidence: Number(e.target.value) })}
            />
          </Field>
          <Field label={`${t("settings.idle")}: ${config.capture.idle_fps}`}>
            <input
              type="range"
              min={0.5}
              max={10}
              step={0.5}
              value={config.capture.idle_fps}
              onChange={(e) => capture({ idle_fps: Number(e.target.value) })}
            />
          </Field>
          <Field label={`${t("settings.active")}: ${config.capture.active_fps}`}>
            <input
              type="range"
              min={1}
              max={15}
              step={1}
              value={config.capture.active_fps}
              onChange={(e) => capture({ active_fps: Number(e.target.value) })}
            />
          </Field>
          <Field label={`${t("settings.threshold")}: ${config.capture.change_threshold}`} hint={t("settings.threshold.hint")}>
            <input
              type="range"
              min={2}
              max={48}
              step={1}
              value={config.capture.change_threshold}
              onChange={(e) => capture({ change_threshold: Number(e.target.value) })}
            />
          </Field>
        </div>
        <label className="check">
          <input
            type="checkbox"
            checked={config.capture.require_foreground}
            onChange={(e) => capture({ require_foreground: e.target.checked })}
          />
          {t("settings.foreground")}
        </label>
        <label className="check">
          <input
            type="checkbox"
            checked={config.gui.open_browser}
            onChange={(e) => setConfig({ ...config, gui: { ...config.gui, open_browser: e.target.checked } })}
          />
          {t("settings.browser")}
        </label>
        <SaveStatus saving={saving} saved={saved} error={error} />
      </Card>
      <Lifecycle onSetup={onSetup} />
    </>
  );
}
