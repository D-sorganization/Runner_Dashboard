/**
 * useSubmit — reducer-driven submit guard (WP20, #4276).
 *
 * States: idle | submitting | succeeded | failed (discriminated union).
 * One idempotency key per user intent; retries after failure reuse that key.
 * A caller whose request can change between attempts passes `intent` (any stable
 * description of the request): a retry with a different intent gets a new key.
 */
import { useCallback, useReducer, useRef } from "react";
import { generateIdempotencyKey } from "../lib/mutationQueue";

export type SubmitIdle = { status: "idle" };
export type SubmitSubmitting = { status: "submitting"; idempotencyKey: string };
export type SubmitSucceeded<T> = { status: "succeeded"; idempotencyKey: string; value: T };
export type SubmitFailed = { status: "failed"; idempotencyKey: string; error: string };

export type SubmitState<T> = SubmitIdle | SubmitSubmitting | SubmitSucceeded<T> | SubmitFailed;

export function assertNever(value: never): never {
  throw new Error(`Unexpected submit state: ${JSON.stringify(value)}`);
}

export function failureTextFromState<T>(state: SubmitState<T>): string | null {
  switch (state.status) {
    case "failed":
      return state.error;
    case "idle":
    case "submitting":
    case "succeeded":
      return null;
    default:
      return assertNever(state);
  }
}

export function isSubmittingState<T>(state: SubmitState<T>): boolean {
  return state.status === "submitting";
}

type SubmitAction<T> =
  | { type: "begin"; idempotencyKey: string }
  | { type: "succeeded"; idempotencyKey: string; value: T }
  | { type: "failed"; idempotencyKey: string; error: string }
  | { type: "reset" };

function reduceSubmit<T>(state: SubmitState<T>, action: SubmitAction<T>): SubmitState<T> {
  switch (action.type) {
    case "begin":
      return { status: "submitting", idempotencyKey: action.idempotencyKey };
    case "succeeded":
      return { status: "succeeded", idempotencyKey: action.idempotencyKey, value: action.value };
    case "failed":
      return { status: "failed", idempotencyKey: action.idempotencyKey, error: action.error };
    case "reset":
      return { status: "idle" };
    default:
      return assertNever(action);
  }
}

export interface SubmitContext {
  idempotencyKey: string;
  signal: AbortSignal;
}

export type SubmitExecutor<T> = (ctx: SubmitContext) => Promise<T>;

export interface UseSubmitResult<T> {
  state: SubmitState<T>;
  isSubmitting: boolean;
  failureText: string | null;
  idempotencyKey: string | null;
  submit: (executor: SubmitExecutor<T>, intent?: string) => void;
  /** Clears state and mints a fresh idempotency key on the next submit. */
  reset: () => void;
}

function errorMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err);
}

/**
 * Parse a failed `fetch` response into an Error (surfaces HTTP status / JSON detail).
 */
export async function throwIfHttpError(response: Response, fallback: string): Promise<void> {
  if (response.ok) return;
  let detail = fallback;
  try {
    const data: unknown = await response.json();
    if (data && typeof data === "object" && "detail" in data) {
      const d = (data as { detail?: unknown }).detail;
      if (typeof d === "string" && d.trim()) detail = d;
    }
  } catch {
    detail = `${fallback} (HTTP ${response.status})`;
  }
  if (!detail.includes(String(response.status))) {
    detail = `HTTP ${response.status}: ${detail}`;
  }
  throw new Error(detail);
}

export function useSubmit<T = void>(): UseSubmitResult<T> {
  const [state, dispatch] = useReducer(reduceSubmit<T>, { status: "idle" });
  const inFlightRef = useRef(false);
  const intentRef = useRef<string | undefined>(undefined);

  const reset = useCallback(() => {
    inFlightRef.current = false;
    dispatch({ type: "reset" });
  }, []);

  const submit = useCallback(
    (executor: SubmitExecutor<T>, intent?: string) => {
      if (inFlightRef.current || state.status === "submitting") {
        return;
      }

      const sameIntent = intentRef.current === intent;
      const idempotencyKey =
        state.status === "failed" && sameIntent ? state.idempotencyKey : generateIdempotencyKey();
      intentRef.current = intent;

      inFlightRef.current = true;
      dispatch({ type: "begin", idempotencyKey });

      const controller = new AbortController();
      executor({ idempotencyKey, signal: controller.signal })
        .then((value) => {
          dispatch({ type: "succeeded", idempotencyKey, value });
        })
        .catch((err: unknown) => {
          dispatch({ type: "failed", idempotencyKey, error: errorMessage(err) });
        })
        .finally(() => {
          inFlightRef.current = false;
        });
    },
    [state],
  );

  const idempotencyKey =
    state.status === "idle"
      ? null
      : state.idempotencyKey;

  return {
    state,
    isSubmitting: isSubmittingState(state),
    failureText: failureTextFromState(state),
    idempotencyKey,
    submit,
    reset,
  };
}
