import type { EvidenceFact, EvidenceSnapshot } from "../api";
import { Card } from "./ui";
import { t, type Key } from "../i18n";

const GROUPS: Array<[Key, (f: EvidenceFact) => boolean]> = [
  ["state.loadout", (f) => ["weapon", "aspect", "keepsake", "familiar"].includes(f.kind)],
  ["state.boons", (f) => ["boon", "hammer", "item"].includes(f.kind)],
  ["state.arcana", (f) => f.kind === "arcana"],
  ["state.run", (f) => ["stat", "room"].includes(f.kind)],
];

const STAT_KEYS: Record<string, Key> = {
  health: "state.health",
  mana: "state.mana",
  last_stands: "state.last_stands",
  depth: "state.depth",
};

function Origin({ origin }: { origin: EvidenceFact["origin"] }) {
  return <span className={`origin o-${origin}`}>{t(`state.origin.${origin}` as Key)}</span>;
}

function label(fact: EvidenceFact): string {
  if (fact.kind === "stat") return `${t(STAT_KEYS[fact.subject] ?? ("state.run" as Key))}: ${fact.value ?? ""}`;
  if (fact.kind === "room") {
    const encounter = fact.details.encounter ? ` · ${fact.details.encounter}` : "";
    return `${fact.label}${encounter}`;
  }
  const rarity = fact.details.Rarity ? ` · ${fact.details.Rarity}` : "";
  const level = fact.details.StackNum && Number(fact.details.StackNum) > 1 ? ` · ${t("state.level")} ${fact.details.StackNum}` : "";
  return `${fact.label}${rarity}${level}`;
}

export function GameState({ evidence }: { evidence: EvidenceSnapshot | null }) {
  if (!evidence || (!evidence.facts.length && !evidence.offers.length && !evidence.save.declared)) return null;
  const summary = evidence.save.summary ?? {};
  const choices = (summary.choices ?? []).filter((c) => c.chosen);
  return (
    <Card title={t("state.title")}>
      {evidence.save.declared && (
        <p className="muted small">
          {evidence.save.at
            ? `${t(summary.in_run ? "state.in_run" : "state.in_hub")} · ${t("state.saved")} ${new Date(
                evidence.save.at * 1000,
              ).toLocaleTimeString()}`
            : evidence.save.error || t("state.no_save")}
        </p>
      )}
      <div className="state-groups">
        {GROUPS.map(([title, filter]) => {
          const facts = evidence.facts.filter(filter);
          if (!facts.length) return null;
          return (
            <div key={title}>
              <h3>{t(title)}</h3>
              <ul className="facts">
                {facts.map((fact) => (
                  <li key={fact.key}>
                    <span>{label(fact)}</span>
                    <Origin origin={fact.origin} />
                  </li>
                ))}
              </ul>
            </div>
          );
        })}
      </div>
      {evidence.offers.length > 0 && (
        <p className="small">
          <span className="muted">{t("state.offers")}: </span>
          {evidence.offers.map((o) => o.label).join(" · ")}
        </p>
      )}
      {choices.length > 0 && (
        <p className="small">
          <span className="muted">
            {t("state.choices")} ({summary.choices_belong_to}):{" "}
          </span>
          {choices
            .slice(-10)
            .map((c) => c.label ?? c.chosen)
            .join(" → ")}
        </p>
      )}
    </Card>
  );
}
