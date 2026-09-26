/**
 * DispatchPlan.tsx — the dry-run plan of a staff dispatch (SC-G5-2, #1498).
 */
import type { RunPlan } from "./staffApi";

export interface DispatchPlanProps {
  plan: RunPlan;
  machine?: string | null;
  forwardedTo?: string | null;
}

export function DispatchPlan({ plan, machine, forwardedTo }: DispatchPlanProps) {
  return (
    <section className="staff-plan" data-testid="assign-plan" aria-label="Dry-run plan">
      <h4 className="staff-plan__title">
        Plan · {plan.role} · {plan.provider}
        {plan.model ? ` (${plan.model})` : ""}
      </h4>
      <dl className="staff-run__facts">
        <dt>Target</dt>
        <dd>
          {plan.target_kind} {plan.target_ref}
          {plan.repo ? ` in ${plan.repo}` : ""}
        </dd>
        <dt>Branch</dt>
        <dd>
          <code data-testid="plan-branch">{plan.branch || "—"}</code>
        </dd>
        <dt>Lease ritual</dt>
        <dd>{plan.lease_ritual ? "yes" : "no"}</dd>
        {plan.consolidation ? (
          <>
            <dt>Consolidation</dt>
            <dd data-testid="plan-consolidation">
              {plan.consolidation.mode} — {plan.consolidation.reason}
            </dd>
          </>
        ) : null}
        <dt>argv</dt>
        <dd>
          <code data-testid="plan-argv">{plan.argv?.join(" ") ?? ""}</code>
        </dd>
        {machine ? (
          <>
            <dt>Machine</dt>
            <dd data-testid="plan-machine">{machine}</dd>
          </>
        ) : null}
        {forwardedTo ? (
          <>
            <dt>Forwarded to</dt>
            <dd data-testid="plan-forwarded">{forwardedTo}</dd>
          </>
        ) : null}
      </dl>
      <details open>
        <summary>Prompt</summary>
        <pre className="staff-pre" data-testid="plan-prompt">
          {plan.prompt}
        </pre>
      </details>
    </section>
  );
}
