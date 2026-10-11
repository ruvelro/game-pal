import type { ReactNode } from "react";
import type { State } from "../api";
import { t } from "../i18n";

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle: string; actions?: ReactNode }) {
  return (
    <header className="page-header">
      <div>
        <h1>{title}</h1>
        <p className="muted">{subtitle}</p>
      </div>
      {actions && <div className="row">{actions}</div>}
    </header>
  );
}

export function Card({ title, children, className = "" }: { title?: string; children: ReactNode; className?: string }) {
  return (
    <section className={`card ${className}`}>
      {title && <h2>{title}</h2>}
      {children}
    </section>
  );
}

const tone: Record<State, string> = {
  idle: "neutral",
  starting: "busy",
  running: "ok",
  ready: "ok",
  paused: "neutral",
  waiting: "warn",
  error: "bad",
};

export function Dot({ state }: { state: State | undefined }) {
  return <span className={`dot ${state ? tone[state] : "neutral"}`} aria-hidden="true" />;
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  );
}

export function SaveBar({ onSave, saved, error }: { onSave: () => void; saved: boolean; error: string | null }) {
  return (
    <div className="save-bar">
      {error && (
        <span className="error-text" role="alert">
          {t("common.error")}: {error}
        </span>
      )}
      {saved && <span className="ok-text">{t("common.saved")}</span>}
      <button className="primary" onClick={onSave}>
        {t("common.save")}
      </button>
    </div>
  );
}
