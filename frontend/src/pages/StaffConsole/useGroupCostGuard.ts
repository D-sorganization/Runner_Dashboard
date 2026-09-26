/**
 * useGroupCostGuard.ts — confirm Board spend before a group turn is sent (SC-D7, #1342).
 *
 * A message to a group thread asks every seat, so it costs more than a direct
 * message. Before sending, this asks the backend for its estimate
 * (`GET /groups/{id}/cost-estimate`). Over the threshold, the send is held and
 * its promise stays open until the user confirms (re-sent with
 * `meta.confirm_cost`) or cancels (resolved `ok: false`, so the composer keeps
 * the draft). Switching threads cancels a held send.
 *
 * The backend guard (`dispatch_group_message`) is authoritative: if the
 * estimate cannot be fetched the message is sent unconfirmed, and the backend
 * still refuses spend over the threshold.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { fetchGroupCostEstimate, type GroupCostEstimate } from "../Staff/staffApi";
import { groupIdOf, isGroupThread } from "./groupTurn";
import type { SendMessagePayload, ThreadInfo } from "./threadTypes";
import type { SendResult } from "./useStaffConsole";

export const NOT_SENT = "Message not sent: the Board cost was not confirmed.";

type Send = (payload: SendMessagePayload) => Promise<SendResult>;

interface Held {
  payload: SendMessagePayload;
  resolve: (result: SendResult) => void;
}

export interface GroupCostGuard {
  send: Send;
  /** The estimate awaiting the user's decision, or null. */
  pending: GroupCostEstimate | null;
  confirm: () => Promise<void>;
  cancel: () => void;
}

export function useGroupCostGuard(thread: ThreadInfo | null, send: Send): GroupCostGuard {
  const [pending, setPending] = useState<GroupCostEstimate | null>(null);
  const held = useRef<Held | null>(null);

  const release = useCallback((): Held | null => {
    const current = held.current;
    held.current = null;
    setPending(null);
    return current;
  }, []);

  const cancel = useCallback(() => {
    release()?.resolve({ ok: false, error: NOT_SENT });
  }, [release]);

  useEffect(() => cancel, [thread?.id, cancel]);

  const guardedSend = useCallback<Send>(
    async (payload) => {
      if (!thread || !isGroupThread(thread) || payload.meta?.confirm_cost) return send(payload);
      let estimate: GroupCostEstimate;
      try {
        estimate = await fetchGroupCostEstimate(groupIdOf(thread), payload.body);
      } catch {
        return send(payload);
      }
      if (!estimate.exceeds_threshold) return send(payload);
      cancel();
      return new Promise<SendResult>((resolve) => {
        held.current = { payload, resolve };
        setPending(estimate);
      });
    },
    [thread, send, cancel],
  );

  const confirm = useCallback(async () => {
    const current = release();
    if (!current) return;
    const { payload, resolve } = current;
    resolve(await send({ ...payload, meta: { ...payload.meta, confirm_cost: true } }));
  }, [release, send]);

  return { send: guardedSend, pending, confirm, cancel };
}
