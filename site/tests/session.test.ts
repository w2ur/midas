import { describe, it, expect } from "vitest";
import {
  DAY_ONE,
  parseDayNumber,
  currentDayNumber,
  dayNumberFor,
} from "../src/lib/session";
import { loadBlogLatest } from "../src/lib/blog";

describe("session", () => {
  it("DAY_ONE is the experiment epoch", () => {
    expect(DAY_ONE).toBe("2026-04-17");
  });

  it("parses the Oracle narrative day number from a column title", () => {
    expect(parseDayNumber("Day 61: The Breakout, the Stop, and the Silence")).toBe(61);
    expect(parseDayNumber("Day 1: Ten Agents Walk Into a Market")).toBe(1);
    expect(parseDayNumber("No number here")).toBeNull();
  });

  it("currentDayNumber tracks the latest Oracle column (never contradicts the story)", () => {
    expect(currentDayNumber()).toBe(parseDayNumber(loadBlogLatest().title));
  });

  it("dayNumberFor returns 1 for the inception session", () => {
    expect(dayNumberFor("2026-04-17")).toBe(1);
  });
});
