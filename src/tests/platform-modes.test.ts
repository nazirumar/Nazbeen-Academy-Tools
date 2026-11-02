import { describe, expect, it } from "vitest";
import {
  MODE_LABELS,
  SETUP_STATUS_META,
  formatUnixTime,
  isValidMode,
  timeframesForMode,
} from "@/lib/platform/modes";

describe("mode profiles", () => {
  it("forex defaults match the product specification", () => {
    expect(timeframesForMode("forex")).toEqual(["D1", "H4", "H1", "M15", "M5", "M1"]);
  });

  it("binary options is M1-only", () => {
    expect(timeframesForMode("binary_options")).toEqual(["M1"]);
  });

  it("validates mode strings", () => {
    expect(isValidMode("forex")).toBe(true);
    expect(isValidMode("binary_options")).toBe(true);
    expect(isValidMode("stocks")).toBe(false);
    expect(isValidMode("")).toBe(false);
  });

  it("labels both modes", () => {
    expect(MODE_LABELS.forex).toBe("Forex");
    expect(MODE_LABELS.binary_options).toBe("Binary Options");
  });
});

describe("setup status metadata", () => {
  it("maps all four statuses to human labels", () => {
    expect(SETUP_STATUS_META.conditions_met.label).toBe("Conditions met");
    expect(SETUP_STATUS_META.conditions_missing.label).toBe("Conditions missing");
    expect(SETUP_STATUS_META.invalidated.label).toBe("Invalidated");
    expect(SETUP_STATUS_META.insufficient_data.label).toBe("Insufficient data");
  });

  it("tones are honest: invalidated is danger, met is success", () => {
    expect(SETUP_STATUS_META.invalidated.tone).toBe("danger");
    expect(SETUP_STATUS_META.conditions_met.tone).toBe("success");
  });
});

describe("formatUnixTime", () => {
  it("formats unix seconds as UTC", () => {
    expect(formatUnixTime(1700000000)).toMatch(/^2023-11-14 22:13 UTC$/);
  });

  it("renders a dash for missing values", () => {
    expect(formatUnixTime(null)).toBe("—");
    expect(formatUnixTime(undefined)).toBe("—");
  });
});
