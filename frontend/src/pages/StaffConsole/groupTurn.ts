/**
 * groupTurn.ts — read a Board group turn from a thread message (SC-D7, #1342).
 *
 * The backend (SC-B9, `staff/groups.py`) stores each seat's reply in
 * `meta.seat_replies`. These helpers are pure: they read what the backend sent
 * and drop anything malformed. They never invent a seat, a price or a
 * proposal field.
 */
import type { CreateProposalPayload } from "../FleetCommand/types";
import type { ThreadInfo, ThreadMessage } from "./threadTypes";

export interface SeatReply {
  seat: string;
  status: string;
  answered: boolean;
  text: string;
  errorDetail: string;
}

export interface GroupTurn {
  quorum: string;
  /** Total spend reported by the backend; null when it did not report one. */
  costUsd: number | null;
  seats: SeatReply[];
}

const DEFAULT_GROUP = "board";

/** Same rule as the backend's `is_group_thread`. */
export function isGroupThread(thread: ThreadInfo | null | undefined): boolean {
  if (!thread) return false;
  return thread.kind === "group" || Boolean(thread.meta?.group) || Boolean(thread.meta?.is_group);
}

export function groupIdOf(thread: ThreadInfo): string {
  const group = thread.meta?.group;
  return typeof group === "string" && group ? group : DEFAULT_GROUP;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function toSeat(key: string, value: unknown): SeatReply | null {
  const raw = asRecord(value);
  if (!raw) return null;
  const status = text(raw.status) || "error";
  return {
    seat: text(raw.seat_name) || key,
    status,
    answered: status === "ok",
    text: text(raw.text),
    errorDetail: text(raw.error_detail),
  };
}

/** The finished group turn in `message`, or null when it is not one (or still pending). */
export function parseGroupTurn(message: ThreadMessage): GroupTurn | null {
  const meta = message.meta;
  if (!meta?.is_group_turn || message.delivery === "pending") return null;
  const replies = asRecord(meta.seat_replies);
  if (!replies) return null;
  const seats = Object.entries(replies)
    .map(([key, value]) => toSeat(key, value))
    .filter((seat): seat is SeatReply => seat !== null);
  const cost = meta.total_cost_usd;
  return {
    quorum: text(meta.quorum),
    costUsd: typeof cost === "number" && Number.isFinite(cost) ? cost : null,
    seats,
  };
}

/** The coordinator's summary without the seat-reply block, which the card renders from structured data. */
export function consensusMarkdown(body: string): string {
  return body.replace(/<details>[\s\S]*?<\/details>/gi, "").trim();
}

function seatLine(seat: SeatReply): string {
  if (seat.answered) return `- ${seat.seat}: ${seat.text}`;
  const why = seat.errorDetail ? `${seat.status}: ${seat.errorDetail}` : seat.status;
  return `- ${seat.seat}: no response (${why})`;
}

/**
 * Board Proposal fields taken from the turn. Only the evidence is known;
 * the title, problem, target repos, urgency and cost are the user's call.
 */
export function proposalPrefill(turn: GroupTurn): Partial<CreateProposalPayload> {
  const heading = turn.quorum ? `Board deliberation, ${turn.quorum}:` : "Board deliberation:";
  return { evidence: [heading, ...turn.seats.map(seatLine)].join("\n") };
}
