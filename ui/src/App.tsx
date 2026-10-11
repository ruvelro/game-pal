import { useEffect, useState } from "react";
import { api, token, type AppConfig } from "./api";
import { t, type Key } from "./i18n";
import { Activity } from "./pages/Activity";
import { Game } from "./pages/Game";
import { Home } from "./pages/Home";
import { Knowledge } from "./pages/Knowledge";
import { Memory } from "./pages/Memory";
import { Models } from "./pages/Models";
import { Output } from "./pages/Output";
import { Settings } from "./pages/Settings";
import { Setup } from "./pages/Setup";
import { Voice } from "./pages/Voice";
import { useLive, useStatus } from "./store";

type Page = "home" | "game" | "knowledge" | "memory" | "voice" | "output" | "models" | "activity" | "settings";

const NAV: Array<[Page, Key, string]> = [
  ["home", "nav.home", "M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"],
  ["game", "nav.game", "M6 9h4M8 7v4M15 10h.01M18 8h.01M7 5h10a5 5 0 0 1 5 5v1a5 5 0 0 1-8.6 3.5L12 13l-1.4 1.5A5 5 0 0 1 2 11v-1a5 5 0 0 1 5-5z"],
  ["knowledge", "nav.knowledge", "M4 5a2 2 0 0 1 2-2h12v16H6a2 2 0 0 0-2 2zM4 5v16M8 7h6M8 11h6"],
  ["memory", "nav.memory", "M12 3a7 7 0 0 0-7 7c0 2.6 1.4 4.4 3 5.6V19h8v-3.4c1.6-1.2 3-3 3-5.6a7 7 0 0 0-7-7zM9 21h6"],
  ["voice", "nav.voice", "M12 3a3 3 0 0 1 3 3v6a3 3 0 0 1-6 0V6a3 3 0 0 1 3-3zM5 11a7 7 0 0 0 14 0M12 18v3"],
  ["output", "nav.output", "M4 5h16v11H4zM8 20h8M12 16v4"],
  ["models", "nav.models", "M12 3l8 4.5v9L12 21l-8-4.5v-9zM12 12l8-4.5M12 12v9M12 12L4 7.5"],
  ["activity", "nav.activity", "M4 20V10M10 20V4M16 20v-7M22 20H2"],
  ["settings", "nav.settings", "M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0M14 4v4M8 10v4M16 16v4"],
];

export function App() {
  if (!token) {
    return (
      <main className="center">
        <img src="./icon.png" alt="" width={64} height={64} />
        <p>{t("error.notoken")}</p>
      </main>
    );
  }
  return <Shell />;
}

function Shell() {
  const [page, setPage] = useState<Page>("home");
  // El asistente de primer uso sale hasta que el jugador lo termina o lo salta.
  const [setup, setSetup] = useState<boolean | null>(null);
  const live = useLive();
  const { status, refresh } = useStatus();

  useEffect(() => {
    api
      .get<AppConfig>("/api/config")
      .then((config) => setSetup(!config.setup_completed))
      .catch(() => setSetup(false));
  }, []);

  const toggle = async () => {
    await api.post(status?.paused ? "/api/watcher/resume" : "/api/watcher/pause");
    await refresh();
  };

  if (setup === null) return null;
  if (setup) {
    return (
      <Setup
        live={live}
        onDone={() => {
          setSetup(false);
          setPage("home");
        }}
      />
    );
  }

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <img src="./icon.png" alt="" width={36} height={36} />
          <span>game-pal</span>
        </div>
        <nav>
          {NAV.map(([id, label, path]) => (
            <button key={id} className={page === id ? "nav active" : "nav"} onClick={() => setPage(id)}>
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d={path} />
              </svg>
              {t(label)}
            </button>
          ))}
        </nav>
        <div className="sidebar-foot">
          <span className={`dot ${live.connected ? "ok" : "warn"}`} aria-hidden="true" />
          <span className="small">{live.connected ? t("status.connected") : t("status.disconnected")}</span>
          <span className="small muted">v{status?.version ?? "…"}</span>
        </div>
      </aside>
      <main className="content">
        {page === "home" && <Home live={live} status={status} onToggle={() => void toggle()} />}
        {page === "game" && <Game />}
        {page === "knowledge" && <Knowledge live={live} />}
        {page === "memory" && <Memory />}
        {page === "voice" && <Voice />}
        {page === "output" && <Output />}
        {page === "models" && <Models />}
        {page === "activity" && <Activity live={live} />}
        {page === "settings" && <Settings onSetup={() => setSetup(true)} />}
      </main>
    </div>
  );
}
