import type { EvidenceFact, EvidenceSnapshot } from "../api";
import { Card } from "./ui";
import { t, type Key } from "../i18n";

// Grupos por tipo de dato, sirvan a un juego o a otro: Hades II trae equipo, bendiciones y arcanas;
// Stardew Valley, habilidades, amistades y objetos. Un tipo sin grupo no se muestra.
const GROUPS: Array<[Key, (f: EvidenceFact) => boolean]> = [
  ["state.loadout", (f) => ["weapon", "aspect", "keepsake", "familiar"].includes(f.kind)],
  ["state.boons", (f) => ["boon", "hammer"].includes(f.kind)],
  ["state.arcana", (f) => f.kind === "arcana"],
  ["state.skills", (f) => f.kind === "skill"],
  ["state.friends", (f) => f.kind === "friend" && f.value !== "spouse"],
  ["state.items", (f) => f.kind === "item"],
  ["state.run", (f) => ["stat", "room"].includes(f.kind)],
];

const STAT_KEYS: Record<string, Key> = {
  health: "state.health",
  mana: "state.mana",
  last_stands: "state.last_stands",
  depth: "state.depth",
  money: "state.money",
  weather: "state.weather",
  daily_luck: "state.daily_luck",
  max_health: "state.max_health",
  max_stamina: "state.max_stamina",
  deepest_mine: "state.deepest_mine",
  house: "state.house",
};

/** Un valor conocido traducido (estaciones, tiempo, suerte, habilidades); si no, tal cual. */
function word(value: unknown): string {
  const text = String(value ?? "");
  return t(`state.v.${text}` as Key) ?? text;
}

function statValue(fact: EvidenceFact): string {
  const value = fact.value ?? "";
  const money = /^(\d+)g$/.exec(value);
  if (money) return `${Number(money[1]).toLocaleString()} g`;
  const tomorrow = fact.details.tomorrow ? ` (${t("state.tomorrow")}: ${word(fact.details.tomorrow)})` : "";
  return `${word(value)}${tomorrow}`;
}

function Origin({ origin }: { origin: EvidenceFact["origin"] }) {
  return <span className={`origin o-${origin}`}>{t(`state.origin.${origin}` as Key)}</span>;
}

function label(fact: EvidenceFact, spouse: string | undefined): string {
  switch (fact.kind) {
    case "stat": {
      const key = STAT_KEYS[fact.subject] as Key | undefined;
      const name = key ? t(key) : fact.subject.replaceAll("_", " ");
      return `${name}: ${statValue(fact)}`;
    }
    case "room": {
      const encounter = fact.details.encounter ? ` · ${fact.details.encounter}` : "";
      return `${fact.label}${encounter}`;
    }
    case "skill":
      return `${word(fact.subject)} · ${t("state.level")} ${fact.value ?? "?"}`;
    case "friend":
      return `${fact.label} · ${fact.value ?? ""}${fact.subject === spouse ? ` · ${t("state.spouse")}` : ""}`;
    case "item": {
      const stack = Number(fact.details.Stack ?? 1);
      return stack > 1 ? `${fact.label} ×${stack}` : fact.label;
    }
  }
  const rarity = fact.details.Rarity ? ` · ${fact.details.Rarity}` : "";
  const level = fact.details.StackNum && Number(fact.details.StackNum) > 1 ? ` · ${t("state.level")} ${fact.details.StackNum}` : "";
  return `${fact.label}${rarity}${level}`;
}

/** «Primavera 18 · año 3», a partir del dato del día del guardado. */
function dayLabel(fact: EvidenceFact | undefined): string {
  if (!fact) return "";
  const { season, day, year } = fact.details;
  if (season && day) return `${word(season)} ${day}${year ? ` · ${t("state.year")} ${year}` : ""}`;
  return fact.value ?? fact.label;
}

/** Hora si el guardado es de hoy; fecha y hora si es de otro día. */
function savedAt(seconds: number): string {
  const date = new Date(seconds * 1000);
  const today = date.toDateString() === new Date().toDateString();
  return today
    ? `${t("state.saved")} ${date.toLocaleTimeString()}`
    : `${t("state.saved.on")} ${date.toLocaleString()}`;
}

export function GameState({ evidence }: { evidence: EvidenceSnapshot | null }) {
  if (!evidence || (!evidence.facts.length && !evidence.offers.length && !evidence.save.declared)) return null;
  const summary = evidence.save.summary ?? {};
  const choices = (summary.choices ?? []).filter((c) => c.chosen);
  const day = dayLabel(evidence.facts.find((f) => f.kind === "day"));
  const spouse = evidence.facts.find((f) => f.kind === "friend" && f.value === "spouse")?.subject;
  // «En partida» o «En el hub» solo en los juegos por partidas, que lo indican en el guardado.
  const phase = typeof summary.in_run === "boolean" ? t(summary.in_run ? "state.in_run" : "state.in_hub") : "";
  return (
    <Card title={t("state.title")}>
      {(evidence.save.declared || evidence.save.at) && (
        <p className="muted small">
          {evidence.save.at
            ? [day, phase, savedAt(evidence.save.at)].filter(Boolean).join(" · ")
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
                    <span>{label(fact, spouse)}</span>
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
