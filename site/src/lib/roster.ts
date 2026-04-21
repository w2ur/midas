export type AgentId =
  | "steady-eddie-eur"
  | "steady-eddie-usd"
  | "sharp-shooter-eur"
  | "sharp-shooter-usd"
  | "yolo-sapiens-eur"
  | "yolo-sapiens-usd"
  | "satoshi"
  | "monsieur-forex"
  | "goldfinger"
  | "world";

export type BaseCurrency = "EUR" | "USD" | "mixed";

export type Agent = {
  id: AgentId;
  display_name: string;
  archetype: string;
  base_currency: BaseCurrency;
  universe_summary: string;
};

export const ORACLE_ID = "the-oracle" as const;

export const TRADING_AGENTS: Agent[] = [
  {
    id: "steady-eddie-eur",
    display_name: "Steady Eddie EUR",
    archetype: "Conservative quality, PEA-leaning",
    base_currency: "EUR",
    universe_summary: "STOXX 600 quality large-caps",
  },
  {
    id: "steady-eddie-usd",
    display_name: "Steady Eddie USD",
    archetype: "Conservative quality",
    base_currency: "USD",
    universe_summary: "S&P 500 quality large-caps",
  },
  {
    id: "sharp-shooter-eur",
    display_name: "Sharp Shooter EUR",
    archetype: "Momentum under UCITS handcuffs",
    base_currency: "EUR",
    universe_summary: "EU momentum, 2x UCITS leverage cap",
  },
  {
    id: "sharp-shooter-usd",
    display_name: "Sharp Shooter USD",
    archetype: "Aggressive US momentum",
    base_currency: "USD",
    universe_summary: "S&P 500 + S&P 400 momentum",
  },
  {
    id: "yolo-sapiens-eur",
    display_name: "YOLO Sapiens EUR",
    archetype: "EU cross-asset degen",
    base_currency: "EUR",
    universe_summary: "Anything EU: equities, ETFs, crypto-EUR",
  },
  {
    id: "yolo-sapiens-usd",
    display_name: "YOLO Sapiens USD",
    archetype: "US cross-asset degen",
    base_currency: "USD",
    universe_summary: "Anything US: equities, ETFs, crypto-USD",
  },
  {
    id: "satoshi",
    display_name: "Satoshi",
    archetype: "On-chain crypto specialist",
    base_currency: "EUR",
    universe_summary: "Kraken top-cap crypto-EUR pairs",
  },
  {
    id: "monsieur-forex",
    display_name: "Monsieur Forex",
    archetype: "Central-banker whisperer",
    base_currency: "EUR",
    universe_summary: "Major and minor FX pairs",
  },
  {
    id: "goldfinger",
    display_name: "Goldfinger",
    archetype: "Contrarian commodities",
    base_currency: "EUR",
    universe_summary: "UCITS gold, silver, energy, miners",
  },
  {
    id: "world",
    display_name: "World",
    archetype: "Cross-asset, cross-currency",
    base_currency: "mixed",
    universe_summary: "Anything globally listed, valued in EUR",
  },
];

const BY_ID = new Map<AgentId, Agent>(TRADING_AGENTS.map((a) => [a.id, a]));

export function getAgent(id: AgentId): Agent {
  const a = BY_ID.get(id);
  if (!a) throw new Error(`Unknown agent id: ${id}`);
  return a;
}

export function isTradingAgent(id: string): id is AgentId {
  return BY_ID.has(id as AgentId);
}
