import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, token, type MemoryInfo } from "../api";
import { Card, PageHeader } from "../components/ui";
import { t } from "../i18n";
import { useConfig } from "../store";

function when(timestamp: number): string {
  return new Date(timestamp * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function Session({ id, summary, started, turns }: { id: number; summary: string | null; started: number; turns: number }) {
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<Array<{ question: string; answer: string; kind: string }> | null>(null);
  const toggle = async () => {
    if (!open && detail === null) {
      setDetail(await api.get(`/api/memory/sessions/${id}`));
    }
    setOpen(!open);
  };
  return (
    <li>
      <div className="row session-head">
        <button className="link" onClick={() => void toggle()}>
          {open ? "▾" : "▸"} {when(started)} · {turns} {t("memory.turns")}
        </button>
      </div>
      {summary && <p className="small">{summary}</p>}
      {open && detail && (
        <ol className="answers">
          {detail.map((turn, i) => (
            <li key={i} className={turn.kind}>
              <span className="muted small">{turn.question}</span>
              <p className="small">{turn.answer}</p>
            </li>
          ))}
        </ol>
      )}
    </li>
  );
}

export function Memory() {
  const { config, setConfig, save } = useConfig();
  const [info, setInfo] = useState<MemoryInfo | null>(null);
  const [lesson, setLesson] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .get<MemoryInfo>("/api/memory")
      .then(setInfo)
      .catch((e: Error) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  if (!config) return null;

  const toggle = async (enabled: boolean) => {
    const next = { ...config, memory: { ...config.memory, enabled } };
    setConfig(next);
    if (await save(next)) load();
  };

  const addLesson = async (event: FormEvent) => {
    event.preventDefault();
    if (!lesson.trim()) return;
    await api.post("/api/memory/lessons", { text: lesson.trim() });
    setLesson("");
    load();
  };

  const exportAll = async () => {
    const response = await fetch("/api/memory/export", { headers: { Authorization: `Bearer ${token ?? ""}` } });
    const blob = await response.blob();
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "game-pal-memoria.json";
    link.click();
    URL.revokeObjectURL(link.href);
  };

  const wipe = async () => {
    if (!window.confirm(t("memory.wipe.confirm"))) return;
    await api.del("/api/memory");
    load();
  };

  return (
    <>
      <PageHeader title={t("memory.title")} subtitle={t("memory.subtitle")} />
      {error && <p className="error-text">{error}</p>}
      <Card>
        <label className="check big-check">
          <input type="checkbox" checked={config.memory.enabled} onChange={(e) => void toggle(e.target.checked)} />
          <span>
            <strong>{t("memory.enabled")}</strong>
            <span className="field-hint block">{t("memory.enabled.hint")}</span>
          </span>
        </label>
        <div className="row wrap">
          <button onClick={() => void exportAll()}>{t("memory.export")}</button>
          <button onClick={() => void wipe()} className="danger">
            {t("memory.wipe")}
          </button>
        </div>
      </Card>

      <div className="two-col">
        <Card title={t("memory.lessons")}>
          <p className="muted small">{t("memory.lessons.hint")}</p>
          <form className="ask" onSubmit={addLesson}>
            <input value={lesson} onChange={(e) => setLesson(e.target.value)} placeholder={t("memory.lessons.placeholder")} maxLength={600} />
            <button className="primary" disabled={!lesson.trim()}>
              {t("memory.lessons.add")}
            </button>
          </form>
          <ul className="lessons">
            {info?.lessons.map((item) => (
              <li key={item.id}>
                <span>{item.text}</span>
                <button className="link" onClick={() => void api.del(`/api/memory/lessons/${item.id}`).then(load)}>
                  ✕
                </button>
              </li>
            ))}
          </ul>
        </Card>
        <Card title={t("memory.sessions")}>
          {info && info.sessions.length === 0 && <p className="muted">{t("memory.sessions.empty")}</p>}
          <ul className="sessions">
            {info?.sessions.map((session) => (
              <Session
                key={session.id}
                id={session.id}
                summary={session.summary}
                started={session.started_at}
                turns={session.turns}
              />
            ))}
          </ul>
        </Card>
      </div>
    </>
  );
}
