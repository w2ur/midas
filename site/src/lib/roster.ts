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
  signatureColor: { light: string; dark: string };
};

export const ORACLE_ID = "the-oracle" as const;

// Mirrors roster.yaml (repo root) — update both if the cast or display names change.
export const TRADING_AGENTS: Agent[] = [
  {
    id: "steady-eddie-eur",
    display_name: "Steady Eddie EUR",
    archetype: "Conservative quality, PEA-leaning",
    base_currency: "EUR",
    universe_summary: "STOXX 600 quality large-caps",
    signatureColor: { light: "#1e7a45", dark: "#5fb87e" },
  },
  {
    id: "steady-eddie-usd",
    display_name: "Steady Eddie USD",
    archetype: "Conservative quality",
    base_currency: "USD",
    universe_summary: "S&P 500 quality large-caps",
    signatureColor: { light: "#2a5e8c", dark: "#6fa8d4" },
  },
  {
    id: "sharp-shooter-eur",
    display_name: "Sharp Shooter EUR",
    archetype: "Momentum under UCITS handcuffs",
    base_currency: "EUR",
    universe_summary: "EU momentum, 2x UCITS leverage cap",
    signatureColor: { light: "#b0512f", dark: "#d6745a" },
  },
  {
    id: "sharp-shooter-usd",
    display_name: "Sharp Shooter USD",
    archetype: "Aggressive US momentum",
    base_currency: "USD",
    universe_summary: "S&P 500 + S&P 400 momentum",
    signatureColor: { light: "#5a7012", dark: "#aec43f" },
  },
  {
    id: "yolo-sapiens-eur",
    display_name: "YOLO Sapiens EUR",
    archetype: "EU cross-asset degen",
    base_currency: "EUR",
    universe_summary: "Anything EU: equities, ETFs, crypto-EUR",
    signatureColor: { light: "#6a40c0", dark: "#a487e0" },
  },
  {
    id: "yolo-sapiens-usd",
    display_name: "YOLO Sapiens USD",
    archetype: "US cross-asset degen",
    base_currency: "USD",
    universe_summary: "Anything US: equities, ETFs, crypto-USD",
    signatureColor: { light: "#b0297e", dark: "#d267b0" },
  },
  {
    id: "satoshi",
    display_name: "Satoshi",
    archetype: "On-chain crypto specialist",
    base_currency: "EUR",
    universe_summary: "Kraken top-cap crypto-EUR pairs",
    signatureColor: { light: "#a85a12", dark: "#f2742e" },
  },
  {
    id: "monsieur-forex",
    display_name: "Monsieur Forex",
    archetype: "Central-banker whisperer",
    base_currency: "EUR",
    universe_summary: "Major and minor FX pairs",
    signatureColor: { light: "#3a5bcf", dark: "#6f8fe6" },
  },
  {
    id: "goldfinger",
    display_name: "Goldfinger",
    archetype: "Contrarian commodities",
    base_currency: "EUR",
    universe_summary: "UCITS gold, silver, energy, miners",
    signatureColor: { light: "#7e6a10", dark: "#b5a24a" },
  },
  {
    id: "world",
    display_name: "World",
    archetype: "Cross-asset, cross-currency",
    base_currency: "mixed",
    universe_summary: "Anything globally listed, valued in EUR",
    signatureColor: { light: "#176e62", dark: "#4fb8a8" },
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

export function getAgentMonogram(id: AgentId): string {
  return getAgent(id).display_name.charAt(0).toUpperCase();
}
