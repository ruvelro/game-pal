import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, type AskResult, type Component, type Status } from "../api";
import { Card, Dot, PageHeader } from "../components/ui";
import { t } from "../i18n";
import type { LiveState } from "../store";

const CARDS: Array<[Component, Parameters<typeof t>[0]]> = [
  ["capture", "card.capture"],
  ["ocr", "card.ocr"],
  ["provider", "card.provider"],
  ["profile", "card.profile"],
];

function Preview({ live, paused }: { live: LiveState; paused: boolean }) {
  const [src, setSrc] = useState<string | null>(null);
  const loading = useRef(false);
  const frameId = live.frame?.frame_id;

  useEffect(() => {
    if (frameId === undefined || paused || loading.current) return;
    loading.current = true;
    api
      .get<Blob>("/api/frame.jpg")
      .then((blob) =>
        setSrc((previous) => {
          if (previous) URL.revokeObjectURL(previous);
          return URL.createObjectURL(blob);
        }),
      )
      .catch(() => undefined)
      .finally(() => {
        loading.current = false;
      });
  }, [frameId, paused]);

  return (
    <div className="preview">
      {src ? <img src={src} alt={t("home.preview")} /> : <p className="muted">{t("home.preview.empty")}</p>}
      {live.window && (
        <div className="preview-meta">
          <span>{live.window.title || live.window.process}</span>
          <span>
            {live.window.width}×{live.window.height}
          </span>
        </div>
      )}
    </div>
  );
}

function Ask() {
  const [prompt, setPrompt] = useState("");
  const [screen, setScreen] = useState(true);
  const [busy, setBusy] = useState(false);
  const [answer, setAnswer] = useState<AskResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!prompt.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      setAnswer(await api.post<AskResult>("/api/provider/ask", { prompt, include_screen: screen }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title={t("home.ask")}>
      <form className="ask" onSubmit={submit}>
        <input
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder={t("home.ask.placeholder")}
          maxLength={4000}
        />
        <button className="primary" disabled={busy || !prompt.trim()}>
          {busy ? t("home.ask.thinking") : t("home.ask.send")}
        </button>
      </form>
      <label className="check">
        <input type="checkbox" checked={screen} onChange={(e) => setScreen(e.target.checked)} />
        {t("home.ask.screen")}
      </label>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {answer && (
        <div className="answer">
          <p>{answer.text}</p>
          <span className="muted small">
            {answer.model} · {answer.elapsed_ms} ms
          </span>
        </div>
      )}
    </Card>
  );
}

export function Home({ live, status, onToggle }: { live: LiveState; status: Status | null; onToggle: () => void }) {
  const paused = status?.paused ?? true;
  return (
    <>
      <PageHeader
        title={t("home.title")}
        subtitle={t("home.subtitle")}
        actions={
          <button className={paused ? "primary big" : "big"} onClick={onToggle}>
            {paused ? t("home.start") : t("home.pause")}
          </button>
        }
      />
      <div className="status-grid">
        {CARDS.map(([component, label]) => {
          const event = live.statuses[component];
          return (
            <div className="status-card" key={component}>
              <div className="row">
                <Dot state={event?.state} />
                <strong>{t(label)}</strong>
              </div>
              <span className="muted small">{event?.detail || status?.errors[component] || "—"}</span>
            </div>
          );
        })}
      </div>
      <div className="two-col">
        <Card title={t("home.preview")}>
          <Preview live={live} paused={paused} />
        </Card>
        <Card title={t("home.text")}>
          {live.read && live.read.lines.length > 0 ? (
            <>
              <ul className="ocr-lines">
                {live.read.lines.map((line, i) => (
                  <li key={i} style={{ opacity: 0.45 + line.confidence * 0.55 }}>
                    {line.text}
                  </li>
                ))}
              </ul>
              <span className="muted small">
                {live.read.backend} · {Math.round(live.read.elapsed_ms)} ms · {live.read.lines.length}
              </span>
            </>
          ) : (
            <p className="muted">{t("home.text.empty")}</p>
          )}
        </Card>
      </div>
      <Ask />
    </>
  );
}
