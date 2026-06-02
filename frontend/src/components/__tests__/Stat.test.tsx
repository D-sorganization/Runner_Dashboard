// @vitest-environment jsdom
/**
 * Behaviour tests for components/Stat.tsx — the single metric card.
 *
 * Covers the label/value render, the optional sub-line, the `subTitle` tooltip
 * override, and the string-sub tooltip fallback added in decomp pass 11 (#836)
 * when this canonical copy superseded the legacy App.tsx duplicate.
 */
import "@testing-library/jest-dom/vitest"
import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import { Stat } from "../Stat"

afterEach(cleanup)

describe("Stat", () => {
  it("renders label and value", () => {
    render(<Stat label="Failed runs" value={7} />)
    expect(screen.getByText("Failed runs")).toBeInTheDocument()
    expect(screen.getByText("7")).toBeInTheDocument()
  })

  it("omits the sub-line when no sub is provided", () => {
    const { container } = render(<Stat label="L" value={1} />)
    expect(container.querySelector(".stat-sub")).toBeNull()
  })

  it("titles the sub-line with the sub text when it is a string", () => {
    render(<Stat label="L" value={1} sub="recent dispatches" />)
    const sub = screen.getByText("recent dispatches")
    expect(sub).toHaveClass("stat-sub")
    expect(sub).toHaveAttribute("title", "recent dispatches")
  })

  it("honours an explicit subTitle override", () => {
    render(<Stat label="L" value={1} sub={<em>x</em>} subTitle="full text" />)
    const sub = screen.getByText("x").closest(".stat-sub")!
    expect(sub).toHaveAttribute("title", "full text")
  })

  it("applies the color style to the value", () => {
    render(<Stat label="L" value="9" color="rgb(255, 0, 0)" />)
    expect(screen.getByText("9")).toHaveStyle({ color: "rgb(255, 0, 0)" })
  })
})
