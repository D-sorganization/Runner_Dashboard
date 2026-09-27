/**
 * panelTurn.ts — pure helpers for reading and validating expert panel turns (SC-D/Issue #1635).
 *
 * PURE: No React imports or hooks.
 */
import type { ThreadInfo, ThreadMessage } from "./threadTypes";

export interface PanelTurnMeta {
  round: number;
  expert: string;
  status: "ok" | "error" | "timeout" | "running" | string;
  stance: "agree" | "partly" | "disagree" | string | null;
  position: string | null;
}

export interface RoundGroup {
  round: number;
  messages: ThreadMessage[];
}

export interface PanelFormExpert {
  name: string;
  perspective: string;
  provider?: string;
  model?: string | null;
}

export interface PanelFormState {
  topic: string;
  mode: "debate" | "brainstorm";
  rounds: number;
  experts: PanelFormExpert[];
}

/** Check if a thread is an expert panel thread. */
export function isPanelThread(thread: ThreadInfo | null | undefined): boolean {
  if (!thread) return false;
  return thread.kind === "panel" || Boolean(thread.meta?.panel) || Boolean(thread.meta?.is_panel);
}

/** Read panel turn metadata from a message; returns null for non-panel messages or synthesis. */
export function panelMeta(message: ThreadMessage): PanelTurnMeta | null {
  const meta = message.meta;
  if (!meta || !meta.is_panel_turn) return null;

  const rawRound = meta.panel_round;
  const round = typeof rawRound === "number" ? rawRound : Number(rawRound);
  if (!Number.isFinite(round) || round <= 0) return null;

  const expert = typeof meta.panel_expert === "string" ? meta.panel_expert : "";
  const status = typeof meta.panel_status === "string" ? meta.panel_status : "ok";
  const stance = typeof meta.panel_stance === "string" ? meta.panel_stance : null;
  const position = typeof meta.panel_position === "string" ? meta.panel_position : null;

  return {
    round,
    expert,
    status,
    stance,
    position,
  };
}

/**
 * Group panel turn messages by round number in ascending order.
 * Non-panel messages (e.g. user topic, moderator synthesis) stay outside the returned round groups.
 */
export function groupByRound(messages: ThreadMessage[]): RoundGroup[] {
  const roundsMap = new Map<number, ThreadMessage[]>();
  for (const msg of messages) {
    const meta = panelMeta(msg);
    if (meta && meta.round > 0) {
      let list = roundsMap.get(meta.round);
      if (!list) {
        list = [];
        roundsMap.set(meta.round, list);
      }
      list.push(msg);
    }
  }
  return Array.from(roundsMap.entries())
    .sort(([a], [b]) => a - b)
    .map(([round, msgs]) => ({ round, messages: msgs }));
}

/** Validate panel form state mirroring API rules. */
export function validatePanelForm(form: PanelFormState): string[] {
  const errors: string[] = [];

  if (!form.topic || !form.topic.trim()) {
    errors.push("Topic must not be blank");
  }

  if (!Number.isInteger(form.rounds) || form.rounds < 1 || form.rounds > 6) {
    errors.push("Rounds must be between 1 and 6");
  }

  if (!form.experts || form.experts.length < 3 || form.experts.length > 4) {
    errors.push("Panels require 3 to 4 experts");
  }

  const seenNames = new Set<string>();
  let hasEmptyName = false;
  let hasDuplicateName = false;
  let hasReservedModerator = false;

  if (form.experts) {
    for (const exp of form.experts) {
      const trimmed = exp.name?.trim() ?? "";
      if (!trimmed) {
        hasEmptyName = true;
        continue;
      }
      const lower = trimmed.toLowerCase();
      if (lower === "moderator") {
        hasReservedModerator = true;
      }
      if (seenNames.has(lower)) {
        hasDuplicateName = true;
      }
      seenNames.add(lower);
    }
  }

  if (hasEmptyName) {
    errors.push("Expert names cannot be empty");
  }
  if (hasDuplicateName) {
    errors.push("Expert names must be unique");
  }
  if (hasReservedModerator) {
    errors.push("'Moderator' is reserved for the synthesis turn");
  }

  return errors;
}
