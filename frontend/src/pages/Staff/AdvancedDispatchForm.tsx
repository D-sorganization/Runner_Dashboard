/**
 * AdvancedDispatchForm.tsx — Unified work request dispatch form (SC-G5-2, #1498).
 *
 * Replaces Staff/Assign.tsx and FleetCommand/DispatchPanel.tsx with a single form
 * that submits to the work-request API (POST /api/v1/staff/requests).
 *
 * Supported request kinds:
 * - staff.dispatch (default: role, provider, machine, repo, issue, pr, prompt)
 * - ci.remediate (repo, run_id, machine, prompt)
 * - issue.act (role, repo, issue, machine, prompt)
 * - pr.act (role, repo, pr, machine, prompt)
 * - code_request.dispatch (repo, profile_id, ref, machine, prompt)
 * - assessment.run (repo, machine, prompt)
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { Badge } from "../../primitives/Badge";
import { EmptyState } from "../../primitives/EmptyState";
import { TouchButton } from "../../primitives/TouchButton";
import {
  errorMessage,
  submitWorkRequest,
  type RosterResponse,
  type RunPlan,
  type WorkRequest,
  type RequestTarget,
} from "./staffApi";

export interface AdvancedDispatchFormProps {
  roster: RosterResponse | null;
  /** Pre-selected role (e.g. from a Roster card's Assign button). */
  initialRole?: string;
  initialKind?: string;
  onDispatched: (runId: string) => void;
}

export const WORK_REQUEST_KINDS = [
  { value: "staff.dispatch", label: "Staff Dispatch (Single Role)" },
  { value: "ci.remediate", label: "CI Remediation" },
  { value: "issue.act", label: "Issue Action" },
  { value: "pr.act", label: "PR Action" },
  { value: "code_request.dispatch", label: "Code Request Dispatch" },
  { value: "assessment.run", label: "Assessment Run" },
] as const;

function parseNumber(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const n = Number(trimmed);
  return Number.isInteger(n) && n >= 1 ? n : null;
}

export function AdvancedDispatchForm({
  roster,
  initialRole,
  initialKind = "staff.dispatch",
  onDispatched,
}: AdvancedDispatchFormProps) {
  const roles = useMemo(() => (roster?.roles ?? []).filter((r) => !r.retired && r.dispatchable), [roster]);
  const [kind, setKind] = useState(initialKind);
  const [role, setRole] = useState(initialRole ?? "");
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [profileId, setProfileId] = useState("");
  const [ref, setRef] = useState("");
  const [repo, setRepo] = useState("");
  const [issue, setIssue] = useState("");
  const [pr, setPr] = useState("");
  const [runId, setRunId] = useState("");
  const [prompt, setPrompt] = useState("");
  const [machine, setMachine] = useState("local");
  const [plan, setPlan] = useState<RunPlan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"preview" | "dispatch" | null>(null);

  useEffect(() => {
    if (initialRole) setRole(initialRole);
  }, [initialRole]);

  // Default to first dispatchable role once roster arrives
  useEffect(() => {
    if (!role && roles.length > 0) setRole(roles[0].name);
  }, [role, roles]);

  const spec = roles.find((r) => r.name === role) ?? null;
  const installed = useMemo(() => roster?.providers ?? {}, [roster]);
  const providerOptions = useMemo(() => {
    const list = spec?.providers ?? [];
    return [...list].sort((a, b) => Number(Boolean(installed[b])) - Number(Boolean(installed[a])));
  }, [spec, installed]);

  // Constrain provider to role's options
  useEffect(() => {
    if (providerOptions.length === 0) {
      setProvider("");
    } else if (!providerOptions.includes(provider)) {
      setProvider(providerOptions[0]);
    }
  }, [providerOptions, provider]);

  const showsRole = kind === "staff.dispatch" || kind === "issue.act" || kind === "pr.act";
  const showsProvider = kind === "staff.dispatch";
  const showsModel = kind === "staff.dispatch";
  const showsProfile = kind === "code_request.dispatch";
  const showsRef = kind === "code_request.dispatch";
  const showsIssue = kind === "staff.dispatch" || kind === "issue.act";
  const showsPr = kind === "staff.dispatch" || kind === "pr.act";
  const showsRunId = kind === "ci.remediate";

  const buildTarget = useCallback((): RequestTarget => {
    const t: RequestTarget = { repo: repo.trim(), ref: ref.trim() };
    if (repo.trim()) t.repo = repo.trim();
    if (showsIssue && parseNumber(issue) !== null) t.issue = parseNumber(issue);
    if (showsPr && parseNumber(pr) !== null) t.pr = parseNumber(pr);
    if (showsRunId && parseNumber(runId) !== null) t.run_id = parseNumber(runId);
    if (showsRef && ref.trim()) t.ref = ref.trim();
    return t;
  }, [repo, issue, pr, runId, ref, showsIssue, showsPr, showsRunId, showsRef]);

  const buildRequest = useCallback(
    (dryRun: boolean): WorkRequest => {
      const target = buildTarget();
      const req: WorkRequest = {
        kind,
        machine: machine.trim() || "local",
        target,
        prompt: prompt.trim(),
        dry_run: dryRun,
      };
      if (showsRole && role.trim()) req.role = role.trim();
      if (showsProvider && provider.trim()) req.provider = provider.trim();
      if (showsModel && model.trim()) req.model = model.trim();
      if (showsProfile && profileId.trim()) req.profile_id = profileId.trim();
      return req;
    },
    [kind, machine, buildTarget, prompt, showsRole, role, showsProvider, provider, showsModel, model, showsProfile, profileId],
  );

  const hasValidInput = useMemo(() => {
    if (kind === "staff.dispatch") {
      if (!role) return false;
      const hasIssue = parseNumber(issue) !== null;
      const hasPr = parseNumber(pr) !== null;
      if (hasIssue && hasPr) return false;
      return hasIssue || hasPr || prompt.trim().length > 0;
    }
    if (kind === "ci.remediate") {
      return parseNumber(runId) !== null || prompt.trim().length > 0;
    }
    return prompt.trim().length > 0 || parseNumber(issue) !== null || parseNumber(pr) !== null || ref.trim().length > 0;
  }, [kind, role, issue, pr, runId, ref, prompt]);

  const canSubmit = hasValidInput && busy === null;

  const submit = useCallback(
    (dryRun: boolean) => {
      setBusy(dryRun ? "preview" : "dispatch");
      setError(null);
      const req = buildRequest(dryRun);
      submitWorkRequest(req)
        .then((resp) => {
          setBusy(null);
          if (resp.state === "planned") {
            const rawPlan = (resp.plan || resp.result || null) as RunPlan | null;
            setPlan(rawPlan);
          } else {
            setPlan(null);
            if (resp.run_id) onDispatched(resp.run_id);
          }
        })
        .catch((e: unknown) => {
          setBusy(null);
          setError(errorMessage(e));
        });
    },
    [buildRequest, onDispatched],
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
    <div className="glass-card staff-panel staff-assign" data-testid="advanced-dispatch-form">
      <div className="staff-panel__header">
        <h3 className="staff-panel__title">Advanced Dispatch</h3>
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
          <label className="form-label" htmlFor="staff-dispatch-kind">
            Kind
          </label>
          <select
            id="staff-dispatch-kind"
            className="form-select"
            value={kind}
            onChange={(e) => {
              setKind(e.target.value);
              setPlan(null);
            }}
          >
            {WORK_REQUEST_KINDS.map((k) => (
              <option key={k.value} value={k.value}>
                {k.label}
              </option>
            ))}
          </select>
        </div>

        {showsRole ? (
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
        ) : null}

        {showsProvider ? (
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
        ) : null}

        {showsModel ? (
          <div className="form-row">
            <label className="form-label" htmlFor="staff-assign-model">
              Model
            </label>
            <input
              id="staff-assign-model"
              className="form-input"
              value={model}
              placeholder="default"
              onChange={(e) => setModel(e.target.value)}
            />
          </div>
        ) : null}

        {showsProfile ? (
          <div className="form-row">
            <label className="form-label" htmlFor="staff-assign-profile">
              Profile ID
            </label>
            <input
              id="staff-assign-profile"
              className="form-input"
              value={profileId}
              placeholder="e.g. planner-strong"
              onChange={(e) => setProfileId(e.target.value)}
            />
          </div>
        ) : null}

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

        {showsIssue || showsPr ? (
          <div className="form-row">
            {showsIssue ? (
              <>
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
              </>
            ) : null}
            {showsPr ? (
              <>
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
              </>
            ) : null}
          </div>
        ) : null}

        {showsRunId ? (
          <div className="form-row">
            <label className="form-label" htmlFor="staff-assign-run-id">
              Run ID
            </label>
            <input
              id="staff-assign-run-id"
              className="form-input staff-assign__number"
              inputMode="numeric"
              value={runId}
              onChange={(e) => setRunId(e.target.value)}
            />
          </div>
        ) : null}

        {showsRef ? (
          <div className="form-row">
            <label className="form-label" htmlFor="staff-assign-ref">
              Ref
            </label>
            <input
              id="staff-assign-ref"
              className="form-input"
              value={ref}
              placeholder="branch, commit or tag"
              onChange={(e) => setRef(e.target.value)}
            />
          </div>
        ) : null}

        <div className="form-row staff-assign__prompt-row">
          <label className="form-label" htmlFor="staff-assign-prompt">
            Prompt
          </label>
          <textarea
            id="staff-assign-prompt"
            className="form-input staff-assign__prompt"
            rows={4}
            value={prompt}
            placeholder="Free-text task description or prompt"
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
      {!hasValidInput ? <p className="staff-muted">Give target details (issue, PR, run, ref) or a prompt.</p> : null}

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
              <code data-testid="plan-argv">{plan.argv?.join(" ") ?? ""}</code>
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

export default AdvancedDispatchForm;
