import { useCallback, useEffect, useState, type ReactNode } from "react";
import { api, type SetupCheck, type SetupInfo } from "../api";
import { t } from "../i18n";

const STATE_DOT: Record<SetupCheck["state"], string> = { ok: "ok", warn: "warn", error: "bad", off: "neutral" };

/** Las comprobaciones de /api/setup, refrescadas cada `intervalMs` si se indica. */
export function useSetupInfo(intervalMs?: number) {
  const [info, setInfo] = useState<SetupInfo | null>(null);
  const refresh = useCallback(
    () =>
      api
        .get<SetupInfo>("/api/setup")
        .then(setInfo)
        .catch(() => undefined),
    [],
  );
  useEffect(() => {
    void refresh();
    if (!intervalMs) return;
    const id = window.setInterval(() => void refresh(), intervalMs);
    return () => window.clearInterval(id);
  }, [refresh, intervalMs]);
  return { info, refresh };
}

export function CommandBox({ command }: { command: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    await navigator.clipboard.writeText(command).catch(() => undefined);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  };
  return (
    <div className="command">
      <code>{command}</code>
      <button className="chip" onClick={() => void copy()}>
        {copied ? t("setup.check.copied") : t("setup.check.copy")}
      </button>
    </div>
  );
}

export function CheckRow({ check, action }: { check: SetupCheck; action?: ReactNode }) {
  const fixable = check.state !== "ok";
  return (
    <li>
      <span className={`dot ${STATE_DOT[check.state]}`} aria-hidden="true" />
      <div className="check-body">
        <span>
          <strong>{check.title}</strong> <span className="muted">· {check.detail}</span>
        </span>
        {fixable && check.fix && <span className="small">{check.fix}</span>}
        {fixable && check.command && <CommandBox command={check.command} />}
        {action}
        {check.settings && (
          <button className="chip" onClick={() => void api.post(`/api/setup/settings/${check.settings}`)}>
            {t("setup.check.settings")}
          </button>
        )}
      </div>
    </li>
  );
}
