import { describe, expect, it } from "vitest";

import {
  decodeConfig,
  encodeConfig,
  type SimulateConfig,
} from "../src/lib/simulate-config";

const SAMPLE: SimulateConfig = {
  kind: "signal",
  config: {
    universe: "sp500",
    selector: "golden-cross",
    manager: "trailing-stop",
    max_positions: 20,
    max_position_pct: 10.0,
    min_hold_days: 5,
  },
  start_date: "2018-01-01",
  end_date: "2024-12-31",
  capital: 10000,
  currency: "EUR",
};

describe("simulate-config", () => {
  it("round-trips through encode/decode", () => {
    const encoded = encodeConfig(SAMPLE);
    const decoded = decodeConfig(encoded);
    expect(decoded).toEqual(SAMPLE);
  });

  it("decodes returns null for invalid input", () => {
    expect(decodeConfig("not-base64")).toBeNull();
    expect(decodeConfig("")).toBeNull();
  });

  it("produces URL-safe encodings (no +, /, =)", () => {
    const encoded = encodeConfig(SAMPLE);
    expect(encoded).not.toMatch(/[+/=]/);
  });
});
