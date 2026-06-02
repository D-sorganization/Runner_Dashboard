// @vitest-environment jsdom
/**
 * Behaviour tests for components/Collapse.tsx — the collapsible section wrapper
 * extracted from legacy/App.tsx (#403). Covers default-open/closed state, the
 * header toggle, and the optional icon/badge slots.
 */
import "@testing-library/jest-dom/vitest"
import { afterEach, describe, expect, it } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { Collapse } from "../Collapse"

afterEach(cleanup)

describe("Collapse", () => {
  it("is open by default and shows its body", () => {
    const { container } = render(
      <Collapse title="Section">
        <p>body content</p>
      </Collapse>,
    )
    expect(screen.getByText("body content")).toBeInTheDocument()
    expect(container.querySelector(".section-body")).not.toHaveClass("collapsed")
    expect(container.querySelector(".chevron")).toHaveClass("open")
  })

  it("starts collapsed when defaultOpen is false", () => {
    const { container } = render(
      <Collapse title="Section" defaultOpen={false}>
        <p>hidden body</p>
      </Collapse>,
    )
    expect(container.querySelector(".section-body")).toHaveClass("collapsed")
    expect(container.querySelector(".chevron")).not.toHaveClass("open")
  })

  it("toggles open/closed when the header is clicked", () => {
    const { container } = render(
      <Collapse title="Toggle me">
        <p>body</p>
      </Collapse>,
    )
    const header = container.querySelector(".section-header") as HTMLElement
    const body = container.querySelector(".section-body") as HTMLElement

    fireEvent.click(header)
    expect(body).toHaveClass("collapsed")

    fireEvent.click(header)
    expect(body).not.toHaveClass("collapsed")
  })

  it("renders the optional icon and badge slots", () => {
    render(
      <Collapse title="Titled" icon={<span>icon</span>} badge={7}>
        <p>body</p>
      </Collapse>,
    )
    expect(screen.getByText("icon")).toBeInTheDocument()
    expect(screen.getByText("7")).toBeInTheDocument()
    expect(screen.getByText("Titled")).toBeInTheDocument()
  })
})
