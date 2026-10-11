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

/** Estado del guardado automático, en una esquina de la pantalla. */
export function SaveStatus({ saving, saved, error }: { saving: boolean; saved: boolean; error: string | null }) {
  if (error) {
    return (
      <div className="save-toast bad" role="alert">
        {t("common.error")}: {error}
      </div>
    );
  }
  if (!saving && !saved) return null;
  return <div className="save-toast">{saving ? t("common.saving") : t("common.saved")}</div>;
}

/** Ajustes que casi nadie necesita tocar, plegados por defecto. */
export function Advanced({ children }: { children: ReactNode }) {
  return (
    <details className="advanced">
      <summary>{t("common.advanced")}</summary>
      <div className="advanced-body">{children}</div>
    </details>
  );
}

/** «<ctrl>+<alt>+p» (sintaxis de los atajos globales) → «Ctrl+Alt+P». */
export function formatKey(combo: string): string {
  return combo
    .split("+")
    .map((part) => {
      const key = part.replace(/^<|>$/g, "");
      return key.length === 1 ? key.toUpperCase() : key.charAt(0).toUpperCase() + key.slice(1);
    })
    .join("+");
}
