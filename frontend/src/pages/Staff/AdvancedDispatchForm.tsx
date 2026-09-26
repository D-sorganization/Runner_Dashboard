/**
 * AdvancedDispatchForm.tsx — the one dispatch form, posting to `POST /api/v1/staff/requests`
 * (SC-G5-2, #1498). Replaces the Staff Assign form and the Fleet Command Dispatch form.
 *
 * The kind decides which target fields show (`requestKinds.ts`). Only kinds the backend
 * accepts are listed; later SC-G5-1 slices add theirs to the table. "Preview" posts
 * with `dry_run: true` and renders the plan; "Dispatch" posts for real and hands the
 * run id to the parent. A request that needs approval, or fails, is shown; the
 * user's input is never cleared.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { Badge } from "../../primitives/Badge";
import { EmptyState } from "../../primitives/EmptyState";
import { TouchButton } from "../../primitives/TouchButton";
import { DispatchPlan } from "./DispatchPlan";
import { FIELD_LABELS, REQUEST_KINDS, type TargetField } from "./requestKinds";
import {
  errorMessage,
  submitStaffRequest,
  type RequestTarget,
  type RosterResponse,
  type StaffDispatchResult,
  type WorkRequest,
} from "./staffApi";

export interface AdvancedDispatchFormProps {
  roster: RosterResponse | null;
  /** Pre-selected role (e.g. from a Roster card's Assign button). */
  initialRole?: string;
  initialValues?: Partial<WorkRequest>;
  onDispatched: (runId: string, threadId?: string) => void;
}

function parseNumber(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const n = Number(trimmed);
  return Number.isInteger(n) && n >= 1 ? n : null;
}

export function AdvancedDispatchForm({ roster, initialRole, initialValues, onDispatched }: AdvancedDispatchFormProps) {
  const roles = useMemo(() => (roster?.roles ?? []).filter((r) => !r.retired && r.dispatchable), [roster]);
  const [kindId, setKindId] = useState(() => initialValues?.kind ?? REQUEST_KINDS[0].id);
  const [role, setRole] = useState(() => initialRole ?? initialValues?.role ?? "");
  const [provider, setProvider] = useState(() => initialValues?.provider ?? "");
  const [model, setModel] = useState(() => initialValues?.model ?? "");
  const [machine, setMachine] = useState(() => initialValues?.machine ?? "local");
  const [repo, setRepo] = useState(() => initialValues?.target?.repo ?? "");
  const [numbers, setNumbers] = useState<Record<TargetField, string>>(() => ({
    issue: initialValues?.target?.issue != null ? String(initialValues.target.issue) : "",
    pr: initialValues?.target?.pr != null ? String(initialValues.target.pr) : "",
    run_id: initialValues?.target?.run_id != null ? String(initialValues.target.run_id) : "",
  }));
  const [prompt, setPrompt] = useState(() => initialValues?.prompt ?? "");
  const [preview, setPreview] = useState<StaffDispatchResult | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"preview" | "dispatch" | null>(null);

  useEffect(() => {
    if (initialRole) setRole(initialRole);
  }, [initialRole]);

  useEffect(() => {
    if (initialValues) {
      if (initialValues.kind) setKindId(initialValues.kind);
      if (initialValues.role) setRole(initialValues.role);
      if (initialValues.provider) setProvider(initialValues.provider);
      if (initialValues.model) setModel(initialValues.model);
      if (initialValues.machine) setMachine(initialValues.machine);
      if (initialValues.target?.repo) setRepo(initialValues.target.repo);
      if (initialValues.target) {
        setNumbers({
          issue: initialValues.target.issue != null ? String(initialValues.target.issue) : "",
          pr: initialValues.target.pr != null ? String(initialValues.target.pr) : "",
          run_id: initialValues.target.run_id != null ? String(initialValues.target.run_id) : "",
        });
      }
      if (initialValues.prompt) setPrompt(initialValues.prompt);
    }
  }, [initialValues]);

  // Default to the first role once the roster arrives if needed.
  useEffect(() => {
    if (!role && roles.length > 0) setRole(roles[0].name);
  }, [role, roles]);

  const kind = REQUEST_KINDS.find((k) => k.id === kindId) ?? REQUEST_KINDS[0];
  const needsRole = kind.id === "staff.dispatch" || kind.id === "issue.act" || kind.id === "pr.act";
  const spec = roles.find((r) => r.name === role) ?? null;
  const installed = useMemo(() => roster?.providers ?? {}, [roster]);
  const providerOptions = useMemo(() => {
    const list = needsRole ? (spec?.providers ?? []) : Object.keys(installed);
    const sorted = [...list].sort((a, b) => Number(Boolean(installed[b])) - Number(Boolean(installed[a])));
    if (provider && !sorted.includes(provider)) {
      return [provider, ...sorted];
    }
    return sorted;
  }, [needsRole, spec, installed, provider]);

  // Keep the provider inside the role's allowed list.
  useEffect(() => {
    if (providerOptions.length === 0) {
      if (!needsRole && provider) return;
      setProvider("");
    } else if (!providerOptions.includes(provider)) {
      setProvider(providerOptions[0]);
    }
  }, [providerOptions, provider, needsRole]);

  const hasTarget = prompt.trim().length > 0 || kind.fields.some((f) => parseNumber(numbers[f]) !== null);
  const roleOk = !needsRole || Boolean(spec?.dispatchable);
  const canSubmit = roleOk && hasTarget && busy === null;

  const buildRequest = useCallback(
    (dryRun: boolean): WorkRequest => {
      const target: RequestTarget = { repo: repo.trim(), ref: "" };
      for (const f of kind.fields) target[f] = parseNumber(numbers[f]);
      return {
        kind: kind.id,
        role: needsRole ? (role || null) : null,
        provider: provider || null,
        model: model.trim() || null,
        machine: machine.trim() || "local",
        prompt: prompt.trim(),
        dry_run: dryRun,
        target,
      };
    },
    [kind, needsRole, role, provider, model, machine, repo, numbers, prompt],
  );

  const submit = useCallback(
    (dryRun: boolean) => {
      setBusy(dryRun ? "preview" : "dispatch");
      setError(null);
      setNotice(null);
      submitStaffRequest(buildRequest(dryRun))
        .then((resp) => {
          setPreview(dryRun ? (resp.plan ?? null) : null);
          if (resp.state === "approval_required") {
            setNotice(`Awaiting approval: ${resp.approval ?? "the request needs an approver"}`);
          } else if (!dryRun && resp.run_id) {
            if (resp.thread_id) {
              onDispatched(resp.run_id, resp.thread_id);
            } else {
              onDispatched(resp.run_id);
            }
          }
        })
        .catch((e: unknown) => setError(errorMessage(e)))
        .finally(() => setBusy(null));
    },
    [buildRequest, onDispatched],
  );

  if (!roster) {
    return (
      <div className="glass-card staff-panel">
        <p className="staff-muted">Waiting for the roster…</p>
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

  const clearPreview = () => setPreview(null);
  return (
    <div className="glass-card staff-panel staff-assign">
      <div className="staff-panel__header">
        <h3 className="staff-panel__title">Dispatch</h3>
        {needsRole && spec && !spec.dispatchable ? (
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
            value={kind.id}
            onChange={(e) => {
              setKindId(e.target.value);
              clearPreview();
            }}
          >
            {REQUEST_KINDS.map((k) => (
              <option key={k.id} value={k.id}>
                {k.label}
              </option>
            ))}
          </select>
        </div>
        {needsRole ? (
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
                clearPreview();
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
          <label className="form-label" htmlFor="staff-dispatch-model">
            Model
          </label>
          <input
            id="staff-dispatch-model"
            className="form-input"
            value={model}
            placeholder="provider default"
            onChange={(e) => setModel(e.target.value)}
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
            placeholder="local, or a peer node name"
            onChange={(e) => setMachine(e.target.value)}
          />
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
          {kind.fields.map((f) => (
            <span key={f} className="staff-assign__field">
              <label className="form-label" htmlFor={`staff-dispatch-${f}`}>
                {FIELD_LABELS[f]}
              </label>
              <input
                id={`staff-dispatch-${f}`}
                className="form-input staff-assign__number"
                inputMode="numeric"
                value={numbers[f]}
                onChange={(e) => setNumbers((prev) => ({ ...prev, [f]: e.target.value }))}
              />
            </span>
          ))}
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
            placeholder="Free-text task"
            onChange={(e) => setPrompt(e.target.value)}
          />
        </div>
        <div className="staff-assign__actions">
          <TouchButton type="submit" disabled={!canSubmit}>
            {busy === "preview" ? "Previewing…" : "Preview"}
          </TouchButton>
          <TouchButton type="button" variant="primary" disabled={!canSubmit} onClick={() => submit(false)}>
            {busy === "dispatch" ? "Dispatching…" : "Dispatch"}
          </TouchButton>
        </div>
      </form>
      {error ? (
        <p className="staff-error" data-testid="dispatch-error" role="alert">
          {error}
        </p>
      ) : null}
      {notice ? (
        <p className="staff-muted" data-testid="dispatch-notice" role="status">
          {notice}
        </p>
      ) : null}
      {!hasTarget ? <p className="staff-muted">Give an issue, a PR or a prompt.</p> : null}
      {preview?.plan ? (
        <DispatchPlan plan={preview.plan} machine={preview.machine} forwardedTo={preview.forwarded_to} />
      ) : null}
    </div>
  );
}
