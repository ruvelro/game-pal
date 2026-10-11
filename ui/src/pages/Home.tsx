import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, type Component, type Status } from "../api";
import { ScreenData } from "../components/ScreenData";
import { Card, Dot, PageHeader } from "../components/ui";
import { t } from "../i18n";
import type { LiveState } from "../store";

const CARDS: Array<[Component, Parameters<typeof t>[0]]> = [
  ["capture", "card.capture"],
  ["ocr", "card.ocr"],
  ["provider", "card.provider"],
  ["profile", "card.profile"],
  ["voice", "card.voice"],
  ["overlay", "card.overlay"],
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

function Conversation({ live }: { live: LiveState }) {
  const [prompt, setPrompt] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [talking, setTalking] = useState(false);
  const [muted, setMuted] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!prompt.trim()) return;
    setError(null);
    try {
      await api.post("/api/assistant/ask", { text: prompt });
      setPrompt("");
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const ptt = async (down: boolean) => {
    if (down === talking) return;
    setTalking(down);
    try {
      await api.post("/api/voice/ptt", { down });
    } catch (e) {
      setTalking(false);
      setError((e as Error).message);
    }
  };

  const toggleMute = async () => {
    const next = !muted;
    await api.post("/api/proactive", { muted: next }).catch(() => undefined);
    setMuted(next);
  };

  const voiceState = live.voice?.state;
  const busy = live.answers.some((a) => !a.done);

  return (
    <Card title={t("home.answers")}>
      <form className="ask" onSubmit={submit}>
        <input
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder={t("home.ask.placeholder")}
          maxLength={4000}
        />
        <button className="primary" disabled={!prompt.trim()}>
          {t("home.ask.send")}
        </button>
      </form>
      <div className="row wrap voice-row">
        <button
          className={talking ? "talk active" : "talk"}
          onPointerDown={() => void ptt(true)}
          onPointerUp={() => void ptt(false)}
          onPointerLeave={() => void ptt(false)}
        >
          {talking ? t("home.talking") : t("home.talk")}
        </button>
        {(live.speaking || busy) && (
          <button onClick={() => void api.post("/api/assistant/stop")}>{t("home.silence")}</button>
        )}
        <button onClick={() => void toggleMute()}>{muted ? t("home.unmute") : t("home.mute")}</button>
        {voiceState && voiceState !== "off" && (
          <span className={`chip-static ${voiceState === "error" ? "warn" : ""}`}>
            <span className={`dot ${voiceState === "idle" ? "neutral" : "busy"}`} />
            {t(`voice.state.${voiceState}` as Parameters<typeof t>[0])}
          </span>
        )}
      </div>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {live.answers.length === 0 ? (
        <p className="muted">{t("home.answers.empty")}</p>
      ) : (
        <ol className="answers">
          {live.answers.map((answer) => (
            <li key={answer.id} className={answer.kind}>
              <span className="muted small">
                {answer.kind === "proactive" ? "💡 " : "❓ "}
                {answer.title}
              </span>
              <p className={answer.error ? "error-text" : ""}>
                {answer.text || (answer.error ? answer.error : answer.done ? "" : t("home.ask.thinking"))}
              </p>
              {answer.sources.length > 0 && (
                <ul className="sources">
                  {answer.sources.map((source) => (
                    <li key={source.n}>
                      <span className="muted small">[{source.n}]</span>{" "}
                      {source.url.startsWith("http") ? (
                        <a href={source.url} target="_blank" rel="noreferrer noopener">
                          {source.title}
                        </a>
                      ) : (
                        <span>{source.title}</span>
                      )}
                    </li>
                  ))}
                </ul>
              )}
              {answer.done && answer.model && (
                <span className="muted small">
                  {answer.model} · {answer.route}
                </span>
              )}
            </li>
          ))}
        </ol>
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
      <Card title={t("screen.title")}>
        {live.screen ? (
          <>
            <div className="row screen-head">
              <strong className="screen-name">{live.screen.name}</strong>
              <span className="chip-static">
                {live.screen.source === "vision" ? t("screen.vision") : t("screen.signals")} ·{" "}
                {Math.round(live.screen.confidence * 100)} %
              </span>
              {!live.screen.certain && <span className="chip-static warn">{t("screen.uncertain")}</span>}
            </div>
            <ScreenData data={live.screen.data} />
          </>
        ) : (
          <p className="muted">{t("screen.none")}</p>
        )}
      </Card>
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
      <Conversation live={live} />
    </>
  );
}
