import { useCallback, useEffect, useState } from "react";
import { api, type EvidenceSnapshot, type ProfileInfo, type ProfileSummary, type ProfileTest, type WindowSummary } from "../api";
import { ScreenData } from "../components/ScreenData";
import { Card, Field, PageHeader, SaveBar } from "../components/ui";
import { LANGUAGES, t } from "../i18n";
import { useConfig } from "../store";

function ProfileTester() {
  const [result, setResult] = useState<ProfileTest | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      setResult(await api.post<ProfileTest>("/api/profile/test"));
    } catch (e) {
      setResult(null);
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title={t("game.test")}>
      <button onClick={() => void run()} disabled={busy}>
        {t("game.test.run")}
      </button>
      {error && <p className="muted">{error.includes("lectura") ? t("game.test.empty") : error}</p>}
      {result && (
        <div className="test-result">
          <p>
            <span className="muted small">{t("game.test.result")}: </span>
            <strong>{result.screen ?? "—"}</strong>{" "}
            <span className="muted small">
              {Math.round(result.confidence * 100)} % · {result.lines} líneas
            </span>
          </p>
          <ScreenData data={result.data} />
          <table className="candidates">
            <tbody>
              {result.candidates.map((c) => (
                <tr key={c.id} className={c.matched ? "ok" : ""}>
                  <td>
                    <strong>{c.id}</strong>
                    <div className="muted small">{c.description}</div>
                  </td>
                  <td className="num">{Math.round(c.score * 100)} %</td>
                  <td className="small">
                    {c.matched
                      ? `${t("game.test.matched")}: ${c.evidence.slice(0, 3).join(" · ")}`
                      : `${t("game.test.failed")}: ${c.failed.join(", ")}`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

export function Game() {
  const { config, setConfig, save, error, saved } = useConfig();
  const [profiles, setProfiles] = useState<ProfileSummary[]>([]);
  const [windows, setWindows] = useState<WindowSummary[]>([]);
  const [info, setInfo] = useState<ProfileInfo | null>(null);
  const [evidence, setEvidence] = useState<EvidenceSnapshot | null>(null);

  const loadWindows = useCallback(() => {
    api
      .get<WindowSummary[]>("/api/windows")
      .then(setWindows)
      .catch(() => setWindows([]));
  }, []);

  const loadInfo = useCallback(() => {
    api
      .get<ProfileInfo>("/api/profile")
      .then(setInfo)
      .catch(() => setInfo(null));
    api
      .get<EvidenceSnapshot>("/api/evidence")
      .then(setEvidence)
      .catch(() => setEvidence(null));
  }, []);

  useEffect(() => {
    api
      .get<{ profiles: ProfileSummary[] }>("/api/profiles")
      .then((data) => setProfiles(data.profiles))
      .catch(() => undefined);
    loadWindows();
    loadInfo();
  }, [loadWindows, loadInfo]);

  if (!config) return null;
  const selected = profiles.find((p) => p.id === config.profile);
  const gameDir = config.game_dirs[config.profile] ?? "";
  const saveDir = config.save_dirs[config.profile] ?? "";
  const trust = async () => {
    await api.post("/api/profile/trust");
    loadInfo();
  };

  const onSave = async () => {
    if (await save(config)) loadInfo();
  };

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
        {info?.uses_game_text && info.id === config.profile && (
          <Field label={t("game.dir")} hint={t("game.dir.hint")}>
            <input
              value={gameDir}
              placeholder={info.install_dir ?? ""}
              spellCheck={false}
              onChange={(e) =>
                setConfig({ ...config, game_dirs: { ...config.game_dirs, [config.profile]: e.target.value } })
              }
            />
            <span className={info.install_dir ? "ok-text small" : "muted small"}>
              {info.install_dir ? `${t("game.dir.found")}: ${info.install_dir}` : t("game.dir.missing")}
            </span>
          </Field>
        )}
        {evidence?.save.untrusted && (
          <div className="notice">
            <strong>{t("game.trust.title")}</strong>
            <p className="small">{t("game.trust.text")}</p>
            <code className="small">{evidence.save.untrusted.digest.slice(0, 16)}…</code>
            <button onClick={() => void trust()}>{t("game.trust.button")}</button>
          </div>
        )}
        {evidence?.save.declared && info?.id === config.profile && (
          <Field label={t("game.saves")} hint={t("game.saves.hint")}>
            <div className="row">
              <input
                value={saveDir}
                placeholder={evidence.save.folder ?? ""}
                spellCheck={false}
                onChange={(e) =>
                  setConfig({ ...config, save_dirs: { ...config.save_dirs, [config.profile]: e.target.value } })
                }
              />
              <button onClick={() => void api.post("/api/evidence/reload").then(loadInfo)} disabled={!evidence.save.reader}>
                {t("game.saves.reload")}
              </button>
            </div>
            <span className={evidence.save.folder ? "ok-text small" : "muted small"}>
              {evidence.save.folder
                ? `${t("game.dir.found")}: ${evidence.save.folder}`
                : evidence.save.error || t("game.saves.none")}
            </span>
          </Field>
        )}
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
        <SaveBar onSave={() => void onSave()} saved={saved} error={error} />
      </Card>

      {info && info.id === config.profile && Object.keys(info.vocabulary).length > 0 && (
        <Card title={t("game.vocab")}>
          <div className="vocab">
            {Object.entries(info.vocabulary).map(([name, count]) => (
              <span className="chip-static" key={name}>
                {name} <strong>{count}</strong>
              </span>
            ))}
          </div>
          {info.warnings.length > 0 && (
            <>
              <h2 className="spaced">{t("game.warnings")}</h2>
              <ul className="warnings">
                {info.warnings.map((w) => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
            </>
          )}
        </Card>
      )}

      {info && info.screens.length > 0 && <ProfileTester />}
    </>
  );
}
