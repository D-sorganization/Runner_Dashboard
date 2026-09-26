// @vitest-environment jsdom
/**
 * GroupDeliberationCard.test.tsx — Board turn with a failed seat, and the proposal action (SC-D7, #1342).
 */
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GroupDeliberationCard } from "../GroupDeliberationCard";
import { parseGroupTurn } from "../groupTurn";
import { MessageItem } from "../MessageItem";
import { PARTIAL_FAILURE } from "./groupTurnFixtures";

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => vi.unstubAllGlobals());

function renderCard() {
  const turn = parseGroupTurn(PARTIAL_FAILURE);
  if (!turn) throw new Error("fixture must parse");
  return render(<GroupDeliberationCard message={PARTIAL_FAILURE} turn={turn} />);
}

describe("GroupDeliberationCard", () => {
  it("shows the consensus first, then collapsed seat replies with the silent seat marked", () => {
    renderCard();
    const card = screen.getByRole("article", { name: /board deliberation/i });
    expect(within(card).getByText(/2\/3 seats answered \(Alpha, Bravo; Charlie: no response\)/)).toBeInTheDocument();
    expect(within(card).getByText("$0.012")).toBeInTheDocument();

    const details = card.querySelector("details");
    expect(details).not.toBeNull();
    expect(details).not.toHaveAttribute("open");
    expect(within(card).getByText("Seat replies (2 of 3 answered)")).toBeInTheDocument();

    const charlie = screen.getByTestId("seat-reply-charlie");
    expect(within(charlie).getByText("No response")).toBeInTheDocument();
    expect(within(charlie).getByText("timeout: seat timed out after 30s")).toBeInTheDocument();
    expect(within(screen.getByTestId("seat-reply-alpha")).getByText("Ship it.")).toBeInTheDocument();
    expect(within(screen.getByTestId("seat-reply-alpha")).getByText("Answered")).toBeInTheDocument();
  });

  it("does not repeat the seat-reply markdown block inside the consensus", () => {
    renderCard();
    expect(screen.getAllByText("Ship it.")).toHaveLength(1);
  });

  it("opens the Board Proposal form prefilled with the seats' replies and submits it", async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ number: 42, title: "Adopt X" }), {
        status: 201,
        headers: { "Content-Type": "application/json" },
      }),
    );
    renderCard();

    fireEvent.click(screen.getByRole("button", { name: /turn into board proposal/i }));
    const evidence = screen.getByLabelText("Evidence") as HTMLTextAreaElement;
    expect(evidence.value).toContain("- charlie: no response (timeout: seat timed out after 30s)");

    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Adopt X" } });
    fireEvent.change(screen.getByLabelText("Problem Statement"), { target: { value: "Should we adopt X?" } });
    fireEvent.change(screen.getByLabelText("Options Considered"), { target: { value: "X, status quo" } });
    fireEvent.change(screen.getByLabelText("Submitter's Lean"), { target: { value: "X" } });
    fireEvent.click(screen.getByRole("button", { name: /submit proposal/i }));

    await waitFor(() => expect(screen.getByText("Proposal #42 submitted successfully!")).toBeInTheDocument());
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/proposals");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string).evidence).toContain("- alpha: Ship it.");
  });

  it("shows no success when the proposal submission fails", async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ detail: "board offline" }), { status: 503 }));
    renderCard();
    fireEvent.click(screen.getByRole("button", { name: /turn into board proposal/i }));
    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Adopt X" } });
    fireEvent.change(screen.getByLabelText("Problem Statement"), { target: { value: "Q" } });
    fireEvent.change(screen.getByLabelText("Options Considered"), { target: { value: "A" } });
    fireEvent.change(screen.getByLabelText("Submitter's Lean"), { target: { value: "A" } });
    fireEvent.click(screen.getByRole("button", { name: /submit proposal/i }));

    await waitFor(() => expect(screen.getByText(/board offline/)).toBeInTheDocument());
    expect(screen.queryByText(/submitted successfully/)).not.toBeInTheDocument();
  });
});

describe("MessageItem routing", () => {
  it("renders a finished group turn as the deliberation card", () => {
    render(<MessageItem message={PARTIAL_FAILURE} />);
    expect(screen.getByRole("article", { name: /board deliberation/i })).toBeInTheDocument();
  });

  it("does not render the card for the pending placeholder", () => {
    render(<MessageItem message={{ ...PARTIAL_FAILURE, delivery: "pending", body_md: "" }} />);
    expect(screen.queryByRole("article", { name: /board deliberation/i })).not.toBeInTheDocument();
  });
});
