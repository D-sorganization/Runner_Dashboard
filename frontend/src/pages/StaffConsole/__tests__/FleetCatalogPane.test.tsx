import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { FleetCatalogPane } from "../FleetCatalogPane";

describe("FleetCatalogPane", () => {
  it("renders skills from the catalog API", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          count: 1,
          skills: [{ name: "fleet-open-pr", version: "1.0.0", pin: "p", roles: [], repos: [], task_types: [] }],
        }),
      }),
    );
    render(<FleetCatalogPane view="skills" />);
    await waitFor(() => {
      expect(screen.getByText("fleet-open-pr")).toBeInTheDocument();
    });
    vi.unstubAllGlobals();
  });
});
