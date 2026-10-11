import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { api, type AppConfig, type KnowledgeInfo, type KnowledgeSource, type SearchHit } from "../api";
import { Card, Field, PageHeader, SaveBar } from "../components/ui";
import { t, type Key } from "../i18n";
import { useConfig, type LiveState } from "../store";

function when(timestamp: number | null): string {
  if (!timestamp) return t("kn.never");
  return new Date(timestamp * 1000).toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" });
}

function SourceRow({ source, live, onChange }: { source: KnowledgeSource; live: LiveState; onChange: () => void }) {
  const progress = live.knowledge[source.id];
  const status = progress && progress.state !== "ready" && progress.state !== "error" ? progress.state : source.status;
  const busy = status === "running" || status === "queued";
  const label = source.kind === "file" ? source.location.replace(/^file:/, "📄 ") : source.location;
  return (
    <tr>
      <td className="location">
        {source.kind === "url" ? (
          <a href={source.location} target="_blank" rel="noreferrer noopener">
            {label}
          </a>
        ) : (
          label
        )}
        <div className="muted small">
          {t(source.origin === "profile" ? "kn.origin.profile" : "kn.origin.user")}
          {source.kind === "url" && ` · ${t("kn.depth").toLowerCase()} ${source.options.depth ?? 1}`}
        </div>
      </td>
      <td>
        <span className={`chip-static ${status === "error" ? "warn" : ""}`}>
          {busy && <span className="dot busy" />}
          {t(`kn.status.${status}` as Key)}
          {status === "running" && progress ? ` · ${progress.pages}` : ""}
        </span>
        {status === "error" && <div className="error-text small">{source.error || progress?.message}</div>}
        {status === "running" && progress?.message && <div className="muted small ellipsis">{progress.message}</div>}
      </td>
      <td className="num small">
        {source.pages} / {source.chunks}
        <div className="muted">{when(source.updated_at)}</div>
      </td>
      <td className="actions">
        <button
          disabled={busy}
          onClick={() => void api.post("/api/knowledge/refresh", { source_id: source.id }).then(onChange)}
        >
          {t("kn.refresh")}
        </button>
        {source.origin === "user" && (
          <button onClick={() => void api.del(`/api/knowledge/sources/${source.id}`).then(onChange)}>
            {t("kn.remove")}
          </button>
        )}
      </td>
    </tr>
  );
}

function Search() {
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!query.trim()) return;
    setHits(await api.get<SearchHit[]>(`/api/knowledge/search?q=${encodeURIComponent(query)}`).catch(() => []));
  };
  return (
    <Card title={t("kn.search")}>
      <form className="ask" onSubmit={submit}>
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder={t("kn.search.placeholder")} />
        <button className="primary" disabled={!query.trim()}>
          {t("home.ask.send")}
        </button>
      </form>
      {hits && hits.length === 0 && <p className="muted">{t("kn.search.none")}</p>}
      {hits && hits.length > 0 && (
        <ol className="answers">
          {hits.map((hit, i) => (
            <li key={i}>
              <a className="small" href={hit.url.startsWith("http") ? hit.url : undefined} target="_blank" rel="noreferrer noopener">
                {hit.title}
                {hit.heading ? ` › ${hit.heading}` : ""}
              </a>
              <p className="small">{hit.text}</p>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}

export function Knowledge({ live }: { live: LiveState }) {
  const [info, setInfo] = useState<KnowledgeInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [url, setUrl] = useState("");
  const [depth, setDepth] = useState(1);
  const [maxPages, setMaxPages] = useState(100);
  const files = useRef<HTMLInputElement>(null);
  const { config, setConfig, save, error: saveError, saved } = useConfig();

  const load = useCallback(() => {
    api
      .get<KnowledgeInfo>("/api/knowledge")
      .then((data) => {
        setInfo(data);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  useEffect(load, [load]);
  // Recarga la lista cuando una fuente termina.
  const finished = Object.values(live.knowledge).filter((p) => p.state === "ready" || p.state === "error").length;
  useEffect(load, [finished, load]);

  const addUrl = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    try {
      await api.post("/api/knowledge/sources", { url, depth, max_pages: maxPages });
      setUrl("");
      load();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const upload = async () => {
    const list = files.current?.files;
    if (!list || list.length === 0) return;
    setError(null);
    try {
      await api.upload("/api/knowledge/upload", list);
      if (files.current) files.current.value = "";
      load();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const knowledge = config?.knowledge;
  const update = (patch: Partial<AppConfig["knowledge"]>) =>
    config && setConfig({ ...config, knowledge: { ...config.knowledge, ...patch } });

  return (
    <>
      <PageHeader
        title={t("kn.title")}
        subtitle={t("kn.subtitle")}
        actions={
          info && (
            <span className="chip-static">
              <strong>{info.stats.pages}</strong> / <strong>{info.stats.chunks}</strong> {t("kn.stats")}
            </span>
          )
        }
      />
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}

      <div className="two-col">
        <Card title={t("kn.add")}>
          <form onSubmit={addUrl}>
            <Field label={t("kn.url")}>
              <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://" spellCheck={false} />
            </Field>
            <div className="two-col tight">
              <Field label={t("kn.depth")} hint={t("kn.depth.hint")}>
                <select value={depth} onChange={(e) => setDepth(Number(e.target.value))}>
                  {[0, 1, 2, 3].map((d) => (
                    <option key={d} value={d}>
                      {d}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label={t("kn.maxpages")}>
                <input
                  type="number"
                  min={1}
                  max={2000}
                  value={maxPages}
                  onChange={(e) => setMaxPages(Number(e.target.value))}
                />
              </Field>
            </div>
            <button className="primary" disabled={!/^https?:\/\/.+\..+/.test(url)}>
              {t("kn.add.button")}
            </button>
          </form>
        </Card>
        <Card title={t("kn.files")}>
          <Field label="" hint={t("kn.files.hint")}>
            <input ref={files} type="file" multiple accept=".md,.markdown,.txt,.html,.htm,.pdf" onChange={() => void upload()} />
          </Field>
        </Card>
      </div>

      <Card title={t("kn.sources")}>
        <div className="row wrap">
          <button onClick={() => void api.post("/api/knowledge/refresh", {}).then(load)} disabled={!info?.sources.length}>
            {t("kn.refresh.all")}
          </button>
          {info?.embedder_error && <span className="error-text small">{info.embedder_error}</span>}
        </div>
        {info && info.sources.length === 0 ? (
          <p className="muted">{t("kn.empty")}</p>
        ) : (
          <table className="candidates sources-table">
            <tbody>
              {info?.sources.map((source) => (
                <SourceRow key={source.id} source={source} live={live} onChange={load} />
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <div className="two-col">
        <Search />
        {knowledge && (
          <Card title={t("kn.settings")}>
            <Field label={t("kn.embeddings")}>
              <select
                value={knowledge.embeddings}
                onChange={(e) => update({ embeddings: e.target.value as AppConfig["knowledge"]["embeddings"] })}
              >
                <option value="local">{t("kn.emb.local")}</option>
                <option value="provider">{t("kn.emb.provider")}</option>
                <option value="none">{t("kn.emb.none")}</option>
              </select>
            </Field>
            {knowledge.embeddings === "provider" && (
              <Field label={t("kn.emb.model")}>
                <input
                  value={knowledge.provider_model}
                  onChange={(e) => update({ provider_model: e.target.value })}
                  spellCheck={false}
                />
              </Field>
            )}
            <label className="check">
              <input
                type="checkbox"
                checked={knowledge.rewrite_queries}
                onChange={(e) => update({ rewrite_queries: e.target.checked })}
              />
              {t("kn.rewrite")}
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={knowledge.auto_refresh}
                onChange={(e) => update({ auto_refresh: e.target.checked })}
              />
              {t("kn.autorefresh")} ({info?.refresh})
            </label>
            <SaveBar onSave={() => config && void save(config)} saved={saved} error={saveError} />
          </Card>
        )}
      </div>
    </>
  );
}
