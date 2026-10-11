import { useCallback, useEffect, useState } from "react";
import { api, type ProfileSummary, type WindowSummary } from "../api";
import { Card, Field, PageHeader, SaveBar } from "../components/ui";
import { LANGUAGES, t } from "../i18n";
import { useConfig } from "../store";

export function Game() {
  const { config, setConfig, save, error, saved } = useConfig();
  const [profiles, setProfiles] = useState<ProfileSummary[]>([]);
  const [windows, setWindows] = useState<WindowSummary[]>([]);

  const loadWindows = useCallback(() => {
    api
      .get<WindowSummary[]>("/api/windows")
      .then(setWindows)
      .catch(() => setWindows([]));
  }, []);

  useEffect(() => {
    api
      .get<{ profiles: ProfileSummary[] }>("/api/profiles")
      .then((data) => setProfiles(data.profiles))
      .catch(() => undefined);
    loadWindows();
  }, [loadWindows]);

  if (!config) return null;
  const selected = profiles.find((p) => p.id === config.profile);

  return (
    <>
      <PageHeader title={t("game.title")} subtitle={t("game.subtitle")} />
      <Card>
        <Field label={t("game.profile")} hint={selected?.description}>
          <select value={config.profile} onChange={(e) => setConfig({ ...config, profile: e.target.value })}>
            {profiles.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t("game.window")} hint={t("game.window.hint")}>
          <div className="row">
            <select
              value={config.window_title ?? ""}
              onChange={(e) => setConfig({ ...config, window_title: e.target.value || null })}
            >
              <option value="">{t("game.window.auto")}</option>
              {config.window_title && !windows.some((w) => w.title === config.window_title) && (
                <option value={config.window_title}>{config.window_title}</option>
              )}
              {windows.map((w, i) => (
                <option key={`${w.title}-${i}`} value={w.title}>
                  {w.title} — {w.process} ({w.width}×{w.height})
                </option>
              ))}
            </select>
            <button onClick={loadWindows}>{t("game.window.refresh")}</button>
          </div>
        </Field>
        <div className="two-col tight">
          <Field label={t("game.lang")}>
            <select
              value={config.game_language}
              onChange={(e) => setConfig({ ...config, game_language: e.target.value })}
            >
              {LANGUAGES.map(([code, name]) => (
                <option key={code} value={code}>
                  {name}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t("game.out")}>
            <select
              value={config.output_language}
              onChange={(e) => setConfig({ ...config, output_language: e.target.value })}
            >
              {LANGUAGES.map(([code, name]) => (
                <option key={code} value={code}>
                  {name}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <SaveBar onSave={() => void save(config)} saved={saved} error={error} />
      </Card>
    </>
  );
}
