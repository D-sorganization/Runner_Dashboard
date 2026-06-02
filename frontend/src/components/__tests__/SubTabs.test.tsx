// @vitest-environment jsdom
/**
 * Behaviour tests for components/SubTabs.tsx — the horizontal tab strip
 * extracted from legacy/App.tsx (#403). Exercises controlled, uncontrolled,
 * localStorage-persisted, disabled-tab and right-badge paths.
 */
import "@testing-library/jest-dom/vitest"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { SubTabs } from "../SubTabs"

const TABS = [
  { key: "a", label: "Alpha" },
  { key: "b", label: "Beta", badge: 3 },
  { key: "c", label: "Gamma", disabled: true },
]

afterEach(cleanup)

describe("SubTabs (uncontrolled)", () => {
  beforeEach(() => localStorage.clear())

  it("defaults the active tab to the first entry", () => {
    render(<SubTabs tabs={TABS} />)
    expect(screen.getByText("Alpha").closest("button")).toHaveClass("active")
  })

  it("switches the active tab on click and reports the change", () => {
    const onChange = vi.fn()
    render(<SubTabs tabs={TABS} onChange={onChange} />)

    fireEvent.click(screen.getByText("Beta"))

    expect(onChange).toHaveBeenCalledWith("b")
    expect(screen.getByText("Beta").closest("button")).toHaveClass("active")
  })

  it("renders a badge for tabs that declare one", () => {
    render(<SubTabs tabs={TABS} />)
    expect(screen.getByText("3")).toBeInTheDocument()
  })

  it("does not activate a disabled tab", () => {
    const onChange = vi.fn()
    render(<SubTabs tabs={TABS} onChange={onChange} />)

    const disabled = screen.getByText("Gamma").closest("button")!
    expect(disabled).toBeDisabled()
    fireEvent.click(disabled)
    expect(onChange).not.toHaveBeenCalled()
  })
})

describe("SubTabs (controlled)", () => {
  it("honours the controlled activeKey and does not self-manage state", () => {
    const onChange = vi.fn()
    const { rerender } = render(
      <SubTabs tabs={TABS} activeKey="b" onChange={onChange} />,
    )
    expect(screen.getByText("Beta").closest("button")).toHaveClass("active")

    fireEvent.click(screen.getByText("Alpha"))
    // Parent owns state — click reports up but active stays "b" until rerender.
    expect(onChange).toHaveBeenCalledWith("a")
    expect(screen.getByText("Beta").closest("button")).toHaveClass("active")

    rerender(<SubTabs tabs={TABS} activeKey="a" onChange={onChange} />)
    expect(screen.getByText("Alpha").closest("button")).toHaveClass("active")
  })
})

describe("SubTabs (persisted)", () => {
  beforeEach(() => localStorage.clear())

  it("restores the active tab from localStorage and persists changes", () => {
    localStorage.setItem("subtab:test", "b")
    render(<SubTabs tabs={TABS} storageKey="subtab:test" />)
    expect(screen.getByText("Beta").closest("button")).toHaveClass("active")

    fireEvent.click(screen.getByText("Alpha"))
    expect(localStorage.getItem("subtab:test")).toBe("a")
  })
})

describe("SubTabs (right badge)", () => {
  it("renders the optional right-badge slot", () => {
    render(<SubTabs tabs={TABS} rightBadge={<span>live</span>} />)
    expect(screen.getByText("live")).toBeInTheDocument()
  })
})
