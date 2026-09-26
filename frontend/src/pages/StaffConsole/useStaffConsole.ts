/**
 * useStaffConsole.ts — shared Staff Console state for the desktop and mobile
 * layouts (#1446): roster, the open thread, its history and live stream,
 * sending, and approval decisions.
 *
 * Every backend failure lands in `error` with its kind (roster, thread, send,
 * decision) so the layout shows it; nothing is dropped silently and no thread
 * id is invented client-side (see consoleThreads.ts).
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  createThread,
  decideActionProposal,
  errorMessage,
  fetchRoster,
  fetchThreadMessages,
  fetchThreads,
  postThreadMessage,
} from "../Staff/staffApi";
import { AUTO_ROUTE_ROLE, resolveRoleThread, type ThreadApi } from "./consoleThreads";
import type { ProposalApproveHandler, ProposalDenyHandler } from "./cards/cardTypes";
import type { RoleDetail } from "./contextTypes";
import type { SendMessagePayload, ThreadInfo, ThreadMessage } from "./threadTypes";
import type { StaffRoleItem } from "./types";
import { useGroupCostGuard, type GroupCostGuard } from "./useGroupCostGuard";
import { useThreadStream } from "./useThreadStream";

export type ConsoleErrorKind = "roster" | "thread" | "send" | "decision";

export interface ConsoleError {
  kind: ConsoleErrorKind;
  message: string;
}

export type SendResult = { ok: boolean; [key: string]: unknown };

export interface UseStaffConsoleOptions {
  /** Roster supplied by the page; fetched when absent or empty. */
  roles?: StaffRoleItem[];
  initialRole?: string;
  initialThread?: ThreadInfo;
  initialMessages?: ThreadMessage[];
  /** Live SSE updates for the open thread (default true). */
  streamEnabled?: boolean;
  threadApi?: ThreadApi;
  onSendMessage?: (payload: SendMessagePayload) => Promise<SendResult>;
  onApproveProposal?: ProposalApproveHandler;
  onDenyProposal?: ProposalDenyHandler;
}

export interface StaffConsoleState {
  roles: StaffRoleItem[];
  rosterLoading: boolean;
  selectedRole: string | null;
  activeThread: ThreadInfo | null;
  openingRole: string | null;
  messages: ThreadMessage[];
  isReconnecting: boolean;
  error: ConsoleError | null;
  currentRole: StaffRoleItem;
  roleDetail: RoleDetail;
  openRole: (roleName: string) => Promise<ThreadInfo | null>;
  openThread: (thread: ThreadInfo, roleName?: string) => void;
  closeThread: () => void;
  sendMessage: (payload: SendMessagePayload) => Promise<SendResult>;
  /** A Board message held for cost confirmation (SC-D7); render with `GroupCostConfirm`. */
  costGuard: Pick<GroupCostGuard, "pending" | "confirm" | "cancel">;
  /** Resolves `false` when the decision was refused, so the card can re-enable. */
  approveProposal: (proposalId: string, params?: Record<string, unknown>) => Promise<boolean>;
  denyProposal: (proposalId: string) => Promise<boolean>;
  dismissError: () => void;
}

const FALLBACK_BARB: StaffRoleItem = {
  name: AUTO_ROUTE_ROLE,
  title: "Barb",
  summary: "Fleet Orchestrator and conversational concierge",
  group: "leadership",
  valid: true,
};

const DEFAULT_THREAD_API: ThreadApi = {
  listThreads: (role) => fetchThreads({ role }).then((res) => res.threads ?? []),
  createThread: (body) => createThread(body),
};

const NO_MESSAGES: ThreadMessage[] = [];

export function toRoleDetail(role: StaffRoleItem): RoleDetail {
  return {
    name: role.name,
    title: role.title,
    mandate: role.summary || "",
    providers: Array.isArray(role.providers)
      ? role.providers.map((p) => (typeof p === "string" ? { name: p, signed_in: true } : p))
      : [],
    budget: role.budget
      ? { usd_per_day: role.budget.daily_limit ?? 50, usd_today: role.budget.spend_today ?? 0 }
      : undefined,
    active_runs: [],
    recent_work_items: [],
  };
}

export function useStaffConsole({
  roles: suppliedRoles,
  initialRole,
  initialThread,
  initialMessages = NO_MESSAGES,
  streamEnabled = true,
  threadApi = DEFAULT_THREAD_API,
  onSendMessage,
  onApproveProposal,
  onDenyProposal,
}: UseStaffConsoleOptions = {}): StaffConsoleState {
  const [fetchedRoles, setFetchedRoles] = useState<StaffRoleItem[]>([]);
  const [rosterLoading, setRosterLoading] = useState(false);
  const [selectedRole, setSelectedRole] = useState<string | null>(initialRole ?? null);
  const [activeThread, setActiveThread] = useState<ThreadInfo | null>(initialThread ?? null);
  const [openingRole, setOpeningRole] = useState<string | null>(null);
  const [history, setHistory] = useState<ThreadMessage[]>(initialMessages);
  const [error, setError] = useState<ConsoleError | null>(null);

  const report = useCallback((kind: ConsoleErrorKind, err: unknown) => {
    setError({ kind, message: errorMessage(err) });
  }, []);

  const hasSuppliedRoles = Boolean(suppliedRoles && suppliedRoles.length > 0);
  const roles = hasSuppliedRoles ? (suppliedRoles as StaffRoleItem[]) : fetchedRoles;

  useEffect(() => {
    if (hasSuppliedRoles) return;
    let cancelled = false;
    setRosterLoading(true);
    fetchRoster()
      .then((res) => {
        if (!cancelled) setFetchedRoles((res?.roles ?? []) as StaffRoleItem[]);
      })
      .catch((err: unknown) => {
        if (!cancelled) report("roster", err);
      })
      .finally(() => {
        if (!cancelled) setRosterLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [hasSuppliedRoles, report]);

  // History for the open thread. The seeded thread keeps its seeded messages.
  const threadId = activeThread?.id ?? "";
  const seeded = Boolean(initialThread && threadId === initialThread.id && initialMessages.length > 0);
  useEffect(() => {
    if (!threadId || seeded) return;
    const controller = new AbortController();
    fetchThreadMessages(threadId, controller.signal)
      .then((res) => setHistory(res?.messages ?? []))
      .catch((err: unknown) => {
        if (!controller.signal.aborted) report("thread", err);
      });
    return () => controller.abort();
  }, [threadId, seeded, report]);

  const stream = useThreadStream({
    threadId,
    initialMessages: seeded ? initialMessages : history,
    enabled: streamEnabled && Boolean(threadId),
  });
  const baseMessages = seeded ? initialMessages : history;
  const messages = stream.messages.length > 0 ? stream.messages : baseMessages;

  const roleByName = useCallback((name: string) => roles.find((r) => r.name === name), [roles]);

  const openThread = useCallback((thread: ThreadInfo, roleName?: string) => {
    if (roleName) setSelectedRole(roleName);
    setActiveThread(thread);
  }, []);

  const openRole = useCallback(
    async (roleName: string): Promise<ThreadInfo | null> => {
      setSelectedRole(roleName);
      setOpeningRole(roleName);
      try {
        const title = roleByName(roleName)?.title || roleName;
        const thread = await resolveRoleThread(roleName, title, threadApi);
        setHistory(NO_MESSAGES);
        setActiveThread(thread);
        setError(null);
        return thread;
      } catch (err) {
        report("thread", err);
        return null;
      } finally {
        setOpeningRole(null);
      }
    },
    [roleByName, threadApi, report],
  );

  const closeThread = useCallback(() => setActiveThread(null), []);

  const sendNow = useCallback(
    async (payload: SendMessagePayload): Promise<SendResult> => {
      if (onSendMessage) return onSendMessage(payload);
      if (!activeThread) return { ok: false, error: "No conversation is open" };
      try {
        const message = await postThreadMessage(
          activeThread.id,
          { body: payload.body, meta: payload.meta },
          payload.idempotencyKey,
        );
        return { ok: true, message };
      } catch (err) {
        report("send", err);
        return { ok: false, error: errorMessage(err) };
      }
    },
    [activeThread, onSendMessage, report],
  );

  const { send: sendMessage, pending: costPending, confirm: confirmCost, cancel: cancelCost } = useGroupCostGuard(
    activeThread,
    sendNow,
  );

  const decide = useCallback(
    async (proposalId: string, decision: "approved" | "denied"): Promise<boolean> => {
      try {
        await decideActionProposal(proposalId, decision, `${decision} in the Staff Console`);
        return true;
      } catch (err) {
        report("decision", err);
        return false;
      }
    },
    [report],
  );

  const approveProposal = useCallback(
    async (proposalId: string, params?: Record<string, unknown>) => {
      if (onApproveProposal) return (await onApproveProposal(proposalId, params)) !== false;
      return decide(proposalId, "approved");
    },
    [onApproveProposal, decide],
  );

  const denyProposal = useCallback(
    async (proposalId: string) => {
      if (onDenyProposal) return (await onDenyProposal(proposalId)) !== false;
      return decide(proposalId, "denied");
    },
    [onDenyProposal, decide],
  );

  const barb = roleByName(AUTO_ROUTE_ROLE) ?? FALLBACK_BARB;
  const currentRole = (selectedRole && roleByName(selectedRole)) || barb;
  const roleDetail = useMemo(() => toRoleDetail(currentRole), [currentRole]);

  return {
    roles,
    rosterLoading,
    selectedRole,
    activeThread,
    openingRole,
    messages,
    isReconnecting: stream.isReconnecting,
    error,
    currentRole,
    roleDetail,
    openRole,
    openThread,
    closeThread,
    sendMessage,
    costGuard: { pending: costPending, confirm: confirmCost, cancel: cancelCost },
    approveProposal,
    denyProposal,
    dismissError: () => setError(null),
  };
}
