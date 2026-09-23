/**
 * Assign.tsx — dispatch form for `POST /api/staff/{role}/run` (#1198).
 *
 * Role select comes from the roster; the provider select is limited to the
 * chosen role's providers (installed ones first, marked). "Preview" posts
 * with `dry_run: true` and renders the plan (prompt / argv / branch / lease
 * ritual); "Dispatch" posts for real and hands the new run id to the parent,
 * which navigates to RunDetail.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { Badge } from "../../primitives/Badge";
import { EmptyState } from "../../primitives/EmptyState";
import { TouchButton } from "../../primitives/TouchButton";
import {
  dispatchRun,
  errorMessage,
  type DispatchBody,
  type RosterResponse,
  type RunPlan,
} from "./staffApi";

export interface AssignProps {
  roster: RosterResponse | null;
  /** Pre-selected role (e.g. from a Roster card's Assign button). */
  initialRole?: string;
  onDispatched: (runId: string) => void;
}

function parseNumber(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const n = Number(trimmed);
  return Number.isInteger(n) && n >= 1 ? n : null;
}

export function Assign({ roster, initialRole, onDispatched }: AssignProps) {
  const roles = useMemo(() => (roster?.roles ?? []).filter((r) => !r.retired && r.dispatchable), [roster]);
  const [role, setRole] = useState(initialRole ?? "");
  const [provider, setProvider] = useState("");
  const [repo, setRepo] = useState("");
  const [issue, setIssue] = useState("");
  const [pr, setPr] = useState("");
  const [prompt, setPrompt] = useState("");
  const [machine, setMachine] = useState("local");
  const [plan, setPlan] = useState<RunPlan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"preview" | "dispatch" | null>(null);

  useEffect(() => {
    if (initialRole) setRole(initialRole);
  }, [initialRole]);

  // Default to the first role once the roster arrives.
  useEffect(() => {
    if (!role && roles.length > 0) setRole(roles[0].name);
  }, [role, roles]);

  const spec = roles.find((r) => r.name === role) ?? null;
  const installed = useMemo(() => roster?.providers ?? {}, [roster]);
  const providerOptions = useMemo(() => {
    const list = spec?.providers ?? [];
    return [...list].sort((a, b) => Number(Boolean(installed[b])) - Number(Boolean(installed[a])));
  }, [spec, installed]);

  // Keep the provider inside the role's allowed list.
  useEffect(() => {
    if (providerOptions.length === 0) {
      setProvider("");
    } else if (!providerOptions.includes(provider)) {
      setProvider(providerOptions[0]);
    }
  }, [providerOptions, provider]);

  const body = useCallback(
    (dryRun: boolean): DispatchBody => ({
      provider: provider || null,
      repo: repo.trim(),
      issue: parseNumber(issue),
      pr: parseNumber(pr),
      prompt: prompt.trim(),
      machine: machine.trim() || "local",
      dry_run: dryRun,
    }),
    [provider, repo, issue, pr, prompt, machine],
  );

  const hasTarget = parseNumber(issue) !== null || parseNumber(pr) !== null || prompt.trim().length > 0;
  const canSubmit = Boolean(spec?.dispatchable) && hasTarget && busy === null;

  const submit = useCallback(
    (dryRun: boolean) => {
      if (!role) return;
      setBusy(dryRun ? "preview" : "dispatch");
      setError(null);
      dispatchRun(role, body(dryRun))
        .then((resp) => {
          setBusy(null);
          if (resp.dry_run) {
            setPlan(resp.plan);
          } else {
            setPlan(null);
            onDispatched(resp.run.id);
          }
        })
        .catch((e: unknown) => {
          setBusy(null);
          setError(errorMessage(e));
        });
    },
    [role, body, onDispatched],
  );

  if (!roster) {
    return (
      <div className="glass-card staff-panel">
        <p className="staff-muted">Waiting for the roster...</p>
      </div>
    );
  }
  if (roles.length === 0) {
    return (
      <div className="glass-card staff-panel">
        <EmptyState title="No dispatchable roles" description="The roster has no active roles on this node." />
      </div>
    );
  }

  return (
    <div className="glass-card staff-panel staff-assign">
      <div className="staff-panel__header">
        <h3 className="staff-panel__title">Assign</h3>
        {spec && !spec.dispatchable ? (
          <Badge tone="warning" size="sm">
            role not dispatchable
          </Badge>
        ) : null}
      </div>
      <form
        className="staff-assign__form"
        onSubmit={(e) => {
          e.preventDefault();
          submit(true);
        }}
      >
        <div className="form-row">
          <label className="form-label" htmlFor="staff-assign-role">
            Role
          </label>
          <select
            id="staff-assign-role"
            className="form-select"
            value={role}
            onChange={(e) => {
              setRole(e.target.value);
              setPlan(null);
            }}
          >
            {roles.map((r) => (
              <option key={r.name} value={r.name}>
                {r.title} ({r.name})
              </option>
            ))}
          </select>
        </div>
        <div className="form-row">
          <label className="form-label" htmlFor="staff-assign-provider">
            Provider
          </label>
          <select
            id="staff-assign-provider"
            className="form-select"
            value={provider}
            onChange={(e) => setProvider(e.target.value)}
            disabled={providerOptions.length === 0}
          >
            {providerOptions.map((pid) => (
              <option key={pid} value={pid}>
                {pid}
                {installed[pid] ? " (installed)" : " (not installed)"}
              </option>
            ))}
          </select>
        </div>
        <div className="form-row">
          <label className="form-label" htmlFor="staff-assign-repo">
            Repo
          </label>
          <input
            id="staff-assign-repo"
            className="form-input"
            value={repo}
            placeholder="bare repository name"
            onChange={(e) => setRepo(e.target.value)}
          />
        </div>
        <div className="form-row">
          <label className="form-label" htmlFor="staff-assign-issue">
            Issue #
          </label>
          <input
            id="staff-assign-issue"
            className="form-input staff-assign__number"
            inputMode="numeric"
            value={issue}
            onChange={(e) => setIssue(e.target.value)}
          />
          <label className="form-label" htmlFor="staff-assign-pr">
            PR #
          </label>
          <input
            id="staff-assign-pr"
            className="form-input staff-assign__number"
            inputMode="numeric"
            value={pr}
            onChange={(e) => setPr(e.target.value)}
          />
        </div>
        <div className="form-row staff-assign__prompt-row">
          <label className="form-label" htmlFor="staff-assign-prompt">
            Prompt
          </label>
          <textarea
            id="staff-assign-prompt"
            className="form-input staff-assign__prompt"
            rows={4}
            value={prompt}
            placeholder="Free-text task (used when no issue/PR is given, or appended to it)"
            onChange={(e) => setPrompt(e.target.value)}
          />
        </div>
        <div className="form-row">
          <label className="form-label" htmlFor="staff-assign-machine">
            Machine
          </label>
          <input
            id="staff-assign-machine"
            className="form-input"
            value={machine}
            onChange={(e) => setMachine(e.target.value)}
          />
        </div>
        <div className="staff-assign__actions">
          <TouchButton type="submit" disabled={!canSubmit}>
            {busy === "preview" ? "Previewing..." : "Preview"}
          </TouchButton>
          <TouchButton type="button" variant="primary" disabled={!canSubmit} onClick={() => submit(false)}>
            {busy === "dispatch" ? "Dispatching..." : "Dispatch"}
          </TouchButton>
        </div>
      </form>
      {error ? <p className="staff-error">{error}</p> : null}
      {!hasTarget ? <p className="staff-muted">Give an issue number, a PR number or a prompt.</p> : null}

      {plan ? (
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
              <code data-testid="plan-argv">{plan.argv.join(" ")}</code>
            </dd>
          </dl>
          <details open>
            <summary>Prompt</summary>
            <pre className="staff-pre" data-testid="plan-prompt">
              {plan.prompt}
            </pre>
          </details>
        </section>
      ) : null}
    </div>
  );
}

export default Assign;
