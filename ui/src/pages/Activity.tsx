import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { Card, PageHeader } from "../components/ui";
import { t } from "../i18n";
import type { LiveState } from "../store";

interface ActivityData {
  hours: number;
  since: number;
  total: number;
  local: number;
  cloud: number;
  cost: number;
  providers: Array<{
    provider: string;
    local: number;
    requests: number;
    ok: number;
    avg_ms: number;
    input_tokens: number;
    output_tokens: number;
    cost: number;
    estimated: number;
  }>;
  hourly: Array<{ bucket: number; local: number; requests: number }>;
  recent: Array<{
    id: number;
    at: number;
    role: string;
    provider: string;
    local: number;
    model: string;
    ok: number;
    fallback: number;
    ms: number;
    input_tokens: number | null;
    output_tokens: number | null;
    cost: number | null;
    estimated: number;
    error: string;
  }>;
}

const money = (value: number) => (value < 0.01 && value > 0 ? `< $0.01` : `$${value.toFixed(2)}`);

function Tile({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="tile">
      <span className="tile-label">{label}</span>
      <span className="tile-value">{value}</span>
      {note && <span className="muted small">{note}</span>}
    </div>
  );
}

/** Barras apiladas por hora: local abajo, nube encima, con 2 px de separación y puntas redondeadas. */
function HourlyChart({ data }: { data: ActivityData }) {
  const [hover, setHover] = useState<number | null>(null);
  const buckets = useMemo(() => {
    const count = Math.max(1, Math.min(data.hours, 24 * 7));
    const step = data.hours > 48 ? 24 : 1;
    const groups = Math.ceil(count / step);
    const rows = Array.from({ length: groups }, (_, i) => ({ index: i, local: 0, cloud: 0, start: data.since + i * step * 3600 }));
    for (const entry of data.hourly) {
      const row = rows[Math.min(groups - 1, Math.floor(entry.bucket / step))];
      if (!row) continue;
      if (entry.local) row.local += entry.requests;
      else row.cloud += entry.requests;
    }
    return { rows, step };
  }, [data]);
  const max = Math.max(1, ...buckets.rows.map((r) => r.local + r.cloud));
  const height = 160;
  const label = (start: number) =>
    new Date(start * 1000).toLocaleString(undefined, buckets.step === 24 ? { weekday: "short" } : { hour: "2-digit" });
  const active = hover !== null ? buckets.rows[hover] : null;

  return (
    <div className="viz-root">
      <div className="legend" aria-hidden="true">
        <span>
          <i className="swatch local" /> {t("activity.local")}
        </span>
        <span>
          <i className="swatch cloud" /> {t("activity.cloud")}
        </span>
      </div>
      <div className="bars" style={{ height }} role="img" aria-label={t("activity.chart")} onMouseLeave={() => setHover(null)}>
        {[0.5, 1].map((f) => (
          <div key={f} className="gridline" style={{ bottom: f * height }}>
            <span>{Math.round(max * f)}</span>
          </div>
        ))}
        {buckets.rows.map((row, i) => {
          const localH = (row.local / max) * height;
          const cloudH = (row.cloud / max) * height;
          return (
            <div key={row.index} className="bar-slot" onMouseEnter={() => setHover(i)}>
              <div className="bar-stack">
                {row.cloud > 0 && <div className="seg cloud" style={{ height: cloudH }} />}
                {row.local > 0 && <div className={`seg local ${row.cloud > 0 ? "" : "top"}`} style={{ height: localH }} />}
              </div>
            </div>
          );
        })}
        {active && hover !== null && (
          <div className="tooltip" style={{ left: `${((hover + 0.5) / buckets.rows.length) * 100}%` }}>
            <strong>{label(active.start)}</strong>
            <span>
              <i className="swatch local" /> {t("activity.local")}: {active.local}
            </span>
            <span>
              <i className="swatch cloud" /> {t("activity.cloud")}: {active.cloud}
            </span>
          </div>
        )}
      </div>
      <div className="bar-labels">
        {buckets.rows.map((row, i) => (
          <span key={row.index}>{i % Math.ceil(buckets.rows.length / 8) === 0 ? label(row.start) : ""}</span>
        ))}
      </div>
    </div>
  );
}

export function Activity({ live }: { live: LiveState }) {
  const [hours, setHours] = useState(24);
  const [data, setData] = useState<ActivityData | null>(null);
  const load = useCallback(() => {
    api
      .get<ActivityData>(`/api/activity?hours=${hours}`)
      .then(setData)
      .catch(() => undefined);
  }, [hours]);
  useEffect(load, [load, live.activity]);

  const avgMs = data && data.total ? data.providers.reduce((s, p) => s + p.avg_ms * p.requests, 0) / data.total : 0;
  const estimated = data?.providers.some((p) => p.estimated);

  return (
    <>
      <PageHeader
        title={t("activity.title")}
        subtitle={t("activity.subtitle")}
        actions={
          <select value={hours} onChange={(e) => setHours(Number(e.target.value))} aria-label={t("activity.range")}>
            <option value={24}>{t("activity.24h")}</option>
            <option value={168}>{t("activity.7d")}</option>
          </select>
        }
      />
      {data && (
        <>
          <div className="tiles">
            <Tile label={t("activity.requests")} value={String(data.total)} />
            <Tile
              label={t("activity.local_share")}
              value={data.total ? `${Math.round((data.local / data.total) * 100)} %` : "—"}
              note={`${data.cloud} ${t("activity.in_cloud")}`}
            />
            <Tile label={t("activity.latency")} value={data.total ? `${Math.round(avgMs)} ms` : "—"} />
            <Tile label={t("activity.cost")} value={money(data.cost)} note={estimated ? t("activity.estimated") : undefined} />
          </div>
          <Card title={t("activity.chart")}>
            {data.total ? <HourlyChart data={data} /> : <p className="muted">{t("activity.empty")}</p>}
          </Card>
          <Card title={t("activity.recent")}>
            <table className="candidates activity-table">
              <thead>
                <tr>
                  <th>{t("activity.when")}</th>
                  <th>{t("activity.task")}</th>
                  <th>{t("activity.provider")}</th>
                  <th className="num">ms</th>
                  <th className="num">{t("activity.tokens")}</th>
                  <th className="num">{t("activity.cost")}</th>
                </tr>
              </thead>
              <tbody>
                {data.recent.map((row) => (
                  <tr key={row.id} className={row.ok ? "" : "failed"}>
                    <td className="small">{new Date(row.at * 1000).toLocaleTimeString()}</td>
                    <td className="small">{t(`activity.role.${row.role}` as Parameters<typeof t>[0])}</td>
                    <td className="small">
                      <i className={`swatch ${row.local ? "local" : "cloud"}`} /> {row.provider} · {row.model}
                      {row.fallback ? ` · ${t("activity.fallback")}` : ""}
                      {!row.ok && <div className="error-text">{row.error}</div>}
                    </td>
                    <td className="num small">{Math.round(row.ms)}</td>
                    <td className="num small">
                      {row.input_tokens ?? "—"} / {row.output_tokens ?? "—"}
                    </td>
                    <td className="num small">{row.cost != null ? money(row.cost) + (row.estimated ? "*" : "") : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {estimated && <p className="muted small">* {t("activity.estimated.note")}</p>}
          </Card>
        </>
      )}
    </>
  );
}
