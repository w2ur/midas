export type SignalConfigShape = {
  universe: string;
  selector: string;
  manager: string;
  max_positions: number;
  max_position_pct: number;
  min_hold_days: number;
};

export type SimulateConfig = {
  kind: "signal";
  config: SignalConfigShape;
  start_date: string;
  end_date: string;
  capital: number;
  currency: "EUR" | "USD";
};

function toUrlSafe(b64: string): string {
  return b64.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function fromUrlSafe(b64: string): string {
  const padded = b64 + "=".repeat((4 - (b64.length % 4)) % 4);
  return padded.replace(/-/g, "+").replace(/_/g, "/");
}

export function encodeConfig(config: SimulateConfig): string {
  const json = JSON.stringify(config);
  const b64 =
    typeof btoa === "function"
      ? btoa(unescape(encodeURIComponent(json)))
      : Buffer.from(json, "utf-8").toString("base64");
  return toUrlSafe(b64);
}

export function decodeConfig(encoded: string): SimulateConfig | null {
  if (!encoded) return null;
  try {
    const restored = fromUrlSafe(encoded);
    const json =
      typeof atob === "function"
        ? decodeURIComponent(escape(atob(restored)))
        : Buffer.from(restored, "base64").toString("utf-8");
    const parsed = JSON.parse(json);
    if (parsed?.kind !== "signal") return null;
    return parsed as SimulateConfig;
  } catch {
    return null;
  }
}
