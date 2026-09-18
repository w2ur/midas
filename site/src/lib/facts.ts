// The experiment in six facts — ONE source, two consumers.
//
// /methodology renders all six (MethodologyFacts.astro); the homepage renders
// four of them as its first-screen "honest controls" strip (2026-09-18 clarity
// pass). They read the same array so the two pages cannot disagree on a fill
// count or a session count. Every figure is derived from committed artifacts at
// build time via cadenceStats() / preRegistrationStatus(); nothing here is
// hand-typed, and tests/facts.test.ts pins the derivation against cadence.ts.
import { cadenceStats, preRegistrationStatus } from "./cadence";
import { TRADING_AGENTS } from "./roster";
import { DAY_ONE } from "./session";

export type FactKey = "money" | "desk" | "sessions" | "fills" | "controls" | "skill";

export interface Fact {
  key: FactKey;
  label: string;
  value: string;
  note: string;
}

/** Every book opens at this figure in its own base currency — see roster.yaml. */
export const INITIAL_CAPITAL = 10_000;

function humanDate(iso: string): string {
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-GB", {
    day: "numeric", month: "short", year: "numeric", timeZone: "UTC",
  });
}

export function experimentFacts(): Fact[] {
  const cadence = cadenceStats();
  const prereg = preRegistrationStatus();
  return [
    {
      key: "money",
      label: "Money at risk",
      value: "None",
      note: "Paper simulation throughout. No broker, no capital.",
    },
    {
      key: "desk",
      label: "The desk",
      value: `${TRADING_AGENTS.length} agents`,
      note: `${INITIAL_CAPITAL.toLocaleString("en-GB")} each, in the agent's own base currency.`,
    },
    {
      key: "sessions",
      label: "Sessions",
      value: String(cadence.sessions),
      note: `Weekdays since ${humanDate(DAY_ONE)}.`,
    },
    {
      key: "fills",
      label: "Fills",
      value: String(cadence.totalFills),
      note: `Across ${cadence.daysWithFill} days that saw at least one trade.`,
    },
    {
      key: "controls",
      label: "Controls per agent",
      value: "2",
      note: "A passive benchmark and a coin flip, both run alongside every book.",
    },
    {
      key: "skill",
      label: "Skill claim",
      value: "Not yet",
      note: `Pre-registered: no claim before ${prereg.monthsRequired} months and ${prereg.fillsPerAgentRequired} fills per agent.`,
    },
  ];
}

/** The subset a caller asked for, in the caller's order. Unknown keys are dropped. */
export function pickFacts(keys: FactKey[]): Fact[] {
  const all = experimentFacts();
  return keys.map((k) => all.find((f) => f.key === k)).filter((f): f is Fact => Boolean(f));
}
