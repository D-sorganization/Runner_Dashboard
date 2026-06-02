// Shared types, constants, and helpers for the Maxwell mobile view.
// Extracted from Mobile.tsx to keep that file under the 500-line cap.
import type { CSSProperties } from "react";

export interface MaxwellStatus {
  status?: "running" | "stopped" | "error" | string;
  http_reachable?: boolean;
  binary_found?: boolean;
  service_running?: boolean;
  service_detail?: string;
  http_detail?: string;
  binary_path?: string;
  dashboard_url?: string;
}

export interface MaxwellTask {
  task_id: string;
  status: string;
  started_at?: string | number;
  elapsed_seconds?: number;
}

export interface ChatMessage {
  id: number;
  role: "operator" | "maxwell";
  content: string;
  streaming?: boolean;
  error?: boolean;
}

export type ControlAction = "start" | "stop" | "restart";

export const CHAT_STORE_KEY = "maxwellMobileChatHistory";
export const MAX_HISTORY = 40;
export const QUICK_CHIPS = [
  "status",
  "summarize last hour",
  "which runners are blocked?",
];

/**
 * Codebase-flavored quick-chips for the in-app Q&A assistant (issue #838).
 * These prompt the daemon's agentic codebase tools (read_file/grep_files/…)
 * once a repo is selected, rather than asking about live fleet state.
 */
export const CODEBASE_QUICK_CHIPS = [
  "where is the job queue handled?",
  "what does /api/queue do?",
  "how does runner autoscaling work?",
  "where are auth scopes enforced?",
];

export function elapsedLabel(task: MaxwellTask): string {
  if (task.elapsed_seconds !== undefined) {
    const s = Math.floor(task.elapsed_seconds);
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    if (m < 60) return `${m}m ${s % 60}s`;
    return `${Math.floor(m / 60)}h ${m % 60}m`;
  }
  if (task.started_at) {
    const start =
      typeof task.started_at === "number"
        ? task.started_at
        : new Date(task.started_at).getTime();
    if (!isNaN(start)) {
      const s = Math.floor((Date.now() - start) / 1000);
      if (s < 60) return `${s}s`;
      const m = Math.floor(s / 60);
      if (m < 60) return `${m}m ${s % 60}s`;
      return `${Math.floor(m / 60)}h ${m % 60}m`;
    }
  }
  return "—";
}

export function statusPillStyle(status: string): CSSProperties {
  const s = status.toLowerCase();
  if (s === "running") {
    return {
      background: "rgba(63,185,80,0.15)",
      color: "var(--accent-green)",
      border: "1px solid rgba(63,185,80,0.4)",
    };
  }
  if (s === "error") {
    return {
      background: "rgba(248,81,73,0.12)",
      color: "var(--accent-red)",
      border: "1px solid rgba(248,81,73,0.35)",
    };
  }
  // stopped / unknown
  return {
    background: "rgba(139,148,158,0.12)",
    color: "var(--text-muted)",
    border: "1px solid rgba(139,148,158,0.3)",
  };
}

export function statusEmoji(status: string): string {
  const s = status.toLowerCase();
  if (s === "running") return "🟢";
  if (s === "error") return "🔴";
  return "🟡";
}

export function loadChatHistory(): ChatMessage[] {
  try {
    return JSON.parse(sessionStorage.getItem(CHAT_STORE_KEY) || "[]");
  } catch {
    return [];
  }
}

export function saveChatHistory(messages: ChatMessage[]): void {
  try {
    sessionStorage.setItem(
      CHAT_STORE_KEY,
      JSON.stringify(messages.slice(-MAX_HISTORY)),
    );
  } catch {
    // sessionStorage may be unavailable in some contexts
  }
}
