// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest"
import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { ModelCatalogRefreshButton } from "../ModelCatalogRefreshButton"

afterEach(cleanup)

describe("ModelCatalogRefreshButton", () => {
  it("posts to /api/models/refresh and renders cache age and stale fallback state", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ providers: { codex: ["gpt-6.1-terra"] }, age_seconds: 120, stale: true }),
    })
    const onRefreshed = vi.fn()
    render(
      <ModelCatalogRefreshButton
        snapshot={{ providers: { codex: ["gpt-6-luna"] }, age_seconds: 5, stale: false }}
        fetchImpl={fetchMock as unknown as typeof fetch}
        onRefreshed={onRefreshed}
      />,
    )
    expect(screen.getByTestId("model-catalog-age")).toHaveTextContent("Cache age: 5s")
    fireEvent.click(screen.getByTestId("model-catalog-refresh-btn"))
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith("/api/models/refresh", {
        method: "POST",
        headers: { "X-Requested-With": "XMLHttpRequest" },
      })
      expect(screen.getByTestId("model-catalog-age")).toHaveTextContent("Stale cache (120s old)")
    })
    expect(onRefreshed).toHaveBeenCalledTimes(1)
  })
})
