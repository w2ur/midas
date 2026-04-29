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

export const TRADING_AGENTS: Agent[] = [
  {
    id: "steady-eddie-eur",
    display_name: "Steady Eddie EUR",
    archetype: "Conservative quality, PEA-leaning",
    base_currency: "EUR",
    universe_summary: "STOXX 600 quality large-caps",
    signatureColor: { light: "#2e6b3c", dark: "#7bb488" },
  },
  {
    id: "steady-eddie-usd",
    display_name: "Steady Eddie USD",
    archetype: "Conservative quality",
    base_currency: "USD",
    universe_summary: "S&P 500 quality large-caps",
    signatureColor: { light: "#2a4d6b", dark: "#7ba0c4" },
  },
  {
    id: "sharp-shooter-eur",
    display_name: "Sharp Shooter EUR",
    archetype: "Momentum under UCITS handcuffs",
    base_currency: "EUR",
    universe_summary: "EU momentum, 2x UCITS leverage cap",
    signatureColor: { light: "#9b3e1d", dark: "#d68c7e" },
  },
  {
    id: "sharp-shooter-usd",
    display_name: "Sharp Shooter USD",
    archetype: "Aggressive US momentum",
    base_currency: "USD",
    universe_summary: "S&P 500 + S&P 400 momentum",
    signatureColor: { light: "#7d2a24", dark: "#c47a72" },
  },
  {
    id: "yolo-sapiens-eur",
    display_name: "YOLO Sapiens EUR",
    archetype: "EU cross-asset degen",
    base_currency: "EUR",
    universe_summary: "Anything EU: equities, ETFs, crypto-EUR",
    signatureColor: { light: "#8a6a1d", dark: "#d4b572" },
  },
  {
    id: "yolo-sapiens-usd",
    display_name: "YOLO Sapiens USD",
    archetype: "US cross-asset degen",
    base_currency: "USD",
    universe_summary: "Anything US: equities, ETFs, crypto-USD",
    signatureColor: { light: "#8a4d1d", dark: "#d4a172" },
  },
  {
    id: "satoshi",
    display_name: "Satoshi",
    archetype: "On-chain crypto specialist",
    base_currency: "EUR",
    universe_summary: "Kraken top-cap crypto-EUR pairs",
    signatureColor: { light: "#2a2a2a", dark: "#bfb8a8" },
  },
  {
    id: "monsieur-forex",
    display_name: "Monsieur Forex",
    archetype: "Central-banker whisperer",
    base_currency: "EUR",
    universe_summary: "Major and minor FX pairs",
    signatureColor: { light: "#3a4d5a", dark: "#9badb8" },
  },
  {
    id: "goldfinger",
    display_name: "Goldfinger",
    archetype: "Contrarian commodities",
    base_currency: "EUR",
    universe_summary: "UCITS gold, silver, energy, miners",
    signatureColor: { light: "#7a5a1d", dark: "#c9a55b" },
  },
  {
    id: "world",
    display_name: "World",
    archetype: "Cross-asset, cross-currency",
    base_currency: "mixed",
    universe_summary: "Anything globally listed, valued in EUR",
    signatureColor: { light: "#5a3a2a", dark: "#b08877" },
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
