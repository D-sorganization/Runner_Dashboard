/**
 * remediationJules.ts — the manual "Run" dispatch for a Jules workflow shown in
 * the Remediation tab's "Jules Workflow Health" panel.
 *
 * Extracted verbatim (behaviour 1:1) from the legacy `App.tsx` monolith as part
 * of the decomposition epic (#836, pass 11). POSTs a `workflow_dispatch` to the
 * backend and reports the outcome through a caller-supplied status setter so the
 * UI can flash an inline banner (replacing the original inline `.then`/`.catch`).
 */
import { legacyFetch } from "./api";

export interface JulesDispatchMsg {
  type: "success" | "error";
  text: string;
}

/**
 * Dispatches the Jules workflow identified by `workflowFile` on `ref` (default
 * "main"), reporting progress through `setMsg`. The banner auto-clears after
 * 6 s, matching the legacy timing.
 *
 * Pre: `workflowFile` is a non-empty workflow filename.
 * Post: exactly one terminal `setMsg({type})` call fires (success or error).
 */
export function dispatchJulesWorkflow(
  workflowFile: string,
  setMsg: (msg: JulesDispatchMsg | null) => void,
  options: { ref?: string; inputs?: Record<string, unknown> } = {},
): Promise<void> {
  const ref = options.ref ?? "main";
  const inputs = options.inputs ?? {};
  return legacyFetch("/api/agent-remediation/dispatch-jules", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Requested-With": "XMLHttpRequest",
    },
    body: JSON.stringify({ workflow_file: workflowFile, ref, inputs }),
  })
    .then((r: Response) => {
      if (!r.ok) {
        return r.json().then((e: { detail?: string }) => {
          setMsg({
            type: "error",
            text: "Dispatch failed: " + (e.detail || r.status),
          });
          setTimeout(() => setMsg(null), 6000);
        });
      }
      setMsg({ type: "success", text: "Dispatched " + workflowFile });
      setTimeout(() => setMsg(null), 6000);
    })
    .catch((err: unknown) => {
      setMsg({ type: "error", text: "Dispatch error: " + err });
      setTimeout(() => setMsg(null), 6000);
    });
}
