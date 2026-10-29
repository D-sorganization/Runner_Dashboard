// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest"
import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import { RdBarbRelayBanner } from "../RdBarbRelayBanner"

afterEach(cleanup)

describe("RdBarbRelayBanner", () => {
  it("stays hidden before 5 minutes of failure, appears at 5 minutes, and clears on success", () => {
    const { rerender } = render(<RdBarbRelayBanner status={{ state: "failing", failing_since: 1000, queued_count: 1 }} now={1299} />)
    expect(screen.queryByTestId("rd-barb-degraded-banner")).toBeNull()

    rerender(<RdBarbRelayBanner status={{ state: "degraded", failing_since: 1000, queued_count: 2 }} now={1300} />)
    const banner = screen.getByTestId("rd-barb-degraded-banner")
    expect(banner).toHaveTextContent("rd-barb relay degraded")
    expect(banner).toHaveTextContent("2 queued")

    rerender(<RdBarbRelayBanner status={{ state: "ok", failing_since: null, queued_count: 0 }} now={1306} />)
    expect(screen.queryByTestId("rd-barb-degraded-banner")).toBeNull()
  })
})
