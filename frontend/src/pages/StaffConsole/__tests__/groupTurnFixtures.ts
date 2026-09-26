/**
 * groupTurnFixtures.ts — a Board turn where one seat timed out (SC-D7, #1342).
 */
import type { ThreadMessage } from "../threadTypes";

export const PARTIAL_FAILURE: ThreadMessage = {
  id: "m2",
  thread_id: "t1",
  author: "board-secretary",
  author_kind: "role",
  kind: "text",
  delivery: "complete",
  body_md:
    "### Board Deliberation\n\n**Quorum:** 2/3 seats answered\n\n<details>\n<summary>Seat Replies</summary>\n#### Alpha\nShip it.\n</details>",
  meta: {
    is_group_turn: true,
    group: "board",
    quorum: "2/3 seats answered (Alpha, Bravo; Charlie: no response)",
    total_cost_usd: 0.0123,
    seat_replies: {
      alpha: { seat_name: "alpha", status: "ok", text: "Ship it.", error_detail: "", cost_usd: 0.01 },
      bravo: { seat_name: "bravo", status: "ok", text: "Needs a benchmark.", error_detail: "", cost_usd: 0.0023 },
      charlie: { seat_name: "charlie", status: "timeout", text: "", error_detail: "seat timed out after 30s", cost_usd: 0 },
    },
  },
};
