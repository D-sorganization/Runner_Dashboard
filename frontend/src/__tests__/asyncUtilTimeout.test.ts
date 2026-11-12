import { getConfig } from "@testing-library/react";
import { describe, expect, it } from "vitest";

describe("test setup (#3056)", () => {
  it("gives async queries CI-load headroom beyond the 1000ms default", () => {
    expect(getConfig().asyncUtilTimeout).toBeGreaterThanOrEqual(5000);
  });
});
