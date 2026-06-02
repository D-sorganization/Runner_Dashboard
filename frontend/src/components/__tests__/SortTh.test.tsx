// @vitest-environment jsdom
/**
 * Behaviour tests for components/SortTh.tsx — the sortable table-header cell
 * extracted from legacy/App.tsx (#403). Covers the sort-cycle logic, mouse and
 * keyboard activation, and aria-sort/indicator rendering.
 */
import "@testing-library/jest-dom/vitest"
import type React from "react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { SortTh, sortStateNext } from "../SortTh"

afterEach(cleanup)

function renderTh(props: Partial<React.ComponentProps<typeof SortTh>> = {}) {
  const setSort = vi.fn()
  render(
    <table>
      <thead>
        <tr>
          <SortTh label="Name" sortKey="name" setSort={setSort} {...props} />
        </tr>
      </thead>
    </table>,
  )
  return { setSort }
}

describe("sortStateNext", () => {
  it("defaults to ascending for a new key", () => {
    expect(sortStateNext(null, "name")).toEqual({ key: "name", dir: "asc" })
    expect(sortStateNext({ key: "other", dir: "desc" }, "name")).toEqual({
      key: "name",
      dir: "asc",
    })
  })

  it("flips direction when the same key is re-selected", () => {
    expect(sortStateNext({ key: "name", dir: "asc" }, "name")).toEqual({
      key: "name",
      dir: "desc",
    })
    expect(sortStateNext({ key: "name", dir: "desc" }, "name")).toEqual({
      key: "name",
      dir: "asc",
    })
  })
})

describe("SortTh", () => {
  it("renders an inactive header with a neutral indicator", () => {
    renderTh()
    const th = screen.getByRole("button")
    expect(th).toHaveAttribute("aria-sort", "none")
    expect(th).toHaveTextContent("↕")
    expect(th).not.toHaveClass("active")
  })

  it("reflects the active ascending sort state", () => {
    renderTh({ sort: { key: "name", dir: "asc" } })
    const th = screen.getByRole("button")
    expect(th).toHaveAttribute("aria-sort", "ascending")
    expect(th).toHaveClass("active")
    expect(th).toHaveTextContent("↑")
  })

  it("reflects the active descending sort state", () => {
    renderTh({ sort: { key: "name", dir: "desc" } })
    const th = screen.getByRole("button")
    expect(th).toHaveAttribute("aria-sort", "descending")
    expect(th).toHaveTextContent("↓")
  })

  it("cycles sort on click", () => {
    const { setSort } = renderTh({ sort: { key: "name", dir: "asc" } })
    fireEvent.click(screen.getByRole("button"))
    expect(setSort).toHaveBeenCalledWith({ key: "name", dir: "desc" })
  })

  it("cycles sort on Enter and Space keydown", () => {
    const { setSort } = renderTh()
    const th = screen.getByRole("button")
    fireEvent.keyDown(th, { key: "Enter" })
    fireEvent.keyDown(th, { key: " " })
    expect(setSort).toHaveBeenCalledTimes(2)
    expect(setSort).toHaveBeenCalledWith({ key: "name", dir: "asc" })
  })

  it("ignores unrelated keydown events", () => {
    const { setSort } = renderTh()
    fireEvent.keyDown(screen.getByRole("button"), { key: "Tab" })
    expect(setSort).not.toHaveBeenCalled()
  })
})
