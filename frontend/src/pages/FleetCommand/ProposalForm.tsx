/**
 * ProposalForm.tsx — Proposal submission form (#1284, CR-7).
 */
import React, { useEffect, useState } from "react";
import { ApiClientError } from "../../lib/api";
import { TouchButton } from "../../primitives/TouchButton";
import {
  createProposal,
  describeError,
  isConflict,
} from "./fleetApi";
import { PanelFrame } from "./PanelFrame";
import type {
  CreateProposalPayload,
  DuplicateCandidate,
  ProposalEstimatedEffort,
  ProposalUrgency,
} from "./types";

const labelStyle: React.CSSProperties = {
  display: "block",
  fontSize: 12,
  fontWeight: 600,
  marginBottom: 4,
};

const inputStyle: React.CSSProperties = {
  width: "100%",
  padding: "6px 8px",
};

interface ProposalFormProps {
  initialPrefill?: Partial<CreateProposalPayload>;
  onSuccess?: () => void;
}

export function ProposalForm({ initialPrefill, onSuccess }: ProposalFormProps) {
  // Form states
  const [title, setTitle] = useState(initialPrefill?.title || "");
  const [targetRepos, setTargetRepos] = useState(
    Array.isArray(initialPrefill?.target_repos)
      ? initialPrefill.target_repos.join(", ")
      : (initialPrefill?.target_repos as string) || "Runner_Dashboard",
  );
  const [problem, setProblem] = useState(initialPrefill?.problem || "");
  const [evidence, setEvidence] = useState(initialPrefill?.evidence || "");
  const [optionsConsidered, setOptionsConsidered] = useState(
    Array.isArray(initialPrefill?.options_considered)
      ? initialPrefill.options_considered.join(", ")
      : (initialPrefill?.options_considered as string) || "",
  );
  const [lean, setLean] = useState(initialPrefill?.lean || "");
  const [estimatedCost, setEstimatedCost] = useState(
    initialPrefill?.estimated_cost || "Medium",
  );
  const [urgency, setUrgency] = useState(initialPrefill?.urgency || "Routine");
  const [codeRequestUrl, setCodeRequestUrl] = useState(
    initialPrefill?.code_request_url || "",
  );

  const [submitting, setSubmitting] = useState(false);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitNotice, setSubmitNotice] = useState<string | null>(null);
  const [duplicateCandidates, setDuplicateCandidates] = useState<
    DuplicateCandidate[] | null
  >(null);

  // Update fields if initialPrefill changes
  useEffect(() => {
    if (!initialPrefill) return;
    if (initialPrefill.title) setTitle(initialPrefill.title);
    if (initialPrefill.target_repos) {
      setTargetRepos(
        Array.isArray(initialPrefill.target_repos)
          ? initialPrefill.target_repos.join(", ")
          : String(initialPrefill.target_repos),
      );
    }
    if (initialPrefill.problem) setProblem(initialPrefill.problem);
    if (initialPrefill.evidence) setEvidence(initialPrefill.evidence);
    if (initialPrefill.options_considered) {
      setOptionsConsidered(
        Array.isArray(initialPrefill.options_considered)
          ? initialPrefill.options_considered.join(", ")
          : String(initialPrefill.options_considered),
      );
    }
    if (initialPrefill.lean) setLean(initialPrefill.lean);
    if (initialPrefill.estimated_cost) setEstimatedCost(initialPrefill.estimated_cost);
    if (initialPrefill.urgency) setUrgency(initialPrefill.urgency);
    if (initialPrefill.code_request_url) setCodeRequestUrl(initialPrefill.code_request_url);
  }, [initialPrefill]);

  const validate = (): boolean => {
    const required = [title, targetRepos, problem, evidence, optionsConsidered, lean, estimatedCost, urgency];
    if (required.some((v) => !v.trim())) {
      setValidationError("Please fill in all required fields.");
      return false;
    }
    setValidationError(null);
    return true;
  };

  const submit = async (confirmNotDuplicate: boolean = false) => {
    if (!validate()) return;

    setSubmitting(true);
    setSubmitError(null);
    setSubmitNotice(null);

    const payload: CreateProposalPayload = {
      title: title.trim(),
      target_repos: targetRepos
        .split(",")
        .map((r) => r.trim())
        .filter(Boolean),
      problem: problem.trim(),
      evidence: evidence.trim(),
      options_considered: optionsConsidered.trim(),
      lean: lean.trim(),
      estimated_cost: estimatedCost,
      urgency: urgency,
      code_request_url: codeRequestUrl.trim() || undefined,
      confirm_not_duplicate: confirmNotDuplicate,
    };

    try {
      const created = await createProposal(payload);
      setSubmitNotice(`Proposal #${created.number} submitted successfully!`);
      setDuplicateCandidates(null);
      // Reset form
      setTitle("");
      setProblem("");
      setEvidence("");
      setOptionsConsidered("");
      setLean("");
      setEstimatedCost("Medium");
      setUrgency("Routine");
      setCodeRequestUrl("");
      onSuccess?.();
    } catch (err: unknown) {
      if (isConflict(err)) {
        const detail =
          err instanceof ApiClientError && typeof err.detail === "object" && err.detail !== null
            ? (err.detail as { candidates?: DuplicateCandidate[] })
            : {};
        const candidates = detail.candidates || [];
        if (candidates.length > 0) {
          setDuplicateCandidates(candidates);
        } else {
          setSubmitError(describeError(err));
        }
      } else {
        setSubmitError(describeError(err));
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <PanelFrame
      title="Propose to Board"
      testId="proposal-form-frame"
      actions={
        <TouchButton onClick={() => submit(false)} disabled={submitting}>
          {submitting ? "Submitting…" : "Submit Proposal"}
        </TouchButton>
      }
    >
      <p className="staff-muted">
        Suggestions submitted here are created as GitHub issues in{" "}
        <code>Repository_Management</code> labelled <code>board:proposal</code> for
        review by the Board.
      </p>

      {validationError ? (
        <div className="fleet-cmd__notice fleet-cmd__notice--warn" role="alert">
          {validationError}
        </div>
      ) : null}

      {submitError ? (
        <div className="fleet-cmd__notice fleet-cmd__notice--error" role="alert">
          {submitError}
        </div>
      ) : null}

      {submitNotice ? (
        <div className="fleet-cmd__notice fleet-cmd__notice--success" role="status">
          {submitNotice}
        </div>
      ) : null}

      {duplicateCandidates && duplicateCandidates.length > 0 ? (
        <div
          className="fleet-cmd__notice fleet-cmd__notice--warn"
          data-testid="duplicate-warning"
        >
          <strong>Potential duplicate proposals found:</strong>
          <ul style={{ margin: "6px 0 10px 18px", padding: 0 }}>
            {duplicateCandidates.map((c) => (
              <li key={c.number}>
                <a
                  href={c.url}
                  target="_blank"
                  rel="noreferrer noopener"
                  style={{ fontWeight: 600 }}
                >
                  #{c.number}: {c.title}
                </a>
              </li>
            ))}
          </ul>
          <TouchButton
            onClick={() => submit(true)}
            disabled={submitting}
            className="action-btn"
          >
            Confirm Not a Duplicate and Submit
          </TouchButton>
        </div>
      ) : null}

      <div
        className="fleet-cmd__form-grid"
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: "12px",
          marginBottom: "12px",
        }}
      >
        <div>
          <label htmlFor="prop-title" style={labelStyle}>
            Title *
          </label>
          <input
            id="prop-title"
            aria-label="Title"
            className="form-input"
            style={inputStyle}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="e.g. Adopt WebGPU for Visualization"
          />
        </div>

        <div>
          <label htmlFor="prop-repos" style={labelStyle}>
            Target Repo(s) *
          </label>
          <input
            id="prop-repos"
            aria-label="Target Repo(s)"
            className="form-input"
            style={inputStyle}
            value={targetRepos}
            onChange={(e) => setTargetRepos(e.target.value)}
            placeholder="Runner_Dashboard, Repository_Management"
          />
        </div>
      </div>

      <div style={{ marginBottom: "12px" }}>
        <label htmlFor="prop-problem" style={labelStyle}>
          Problem Statement *
        </label>
        <textarea
          id="prop-problem"
          aria-label="Problem Statement"
          className="form-textarea"
          rows={3}
          style={inputStyle}
          value={problem}
          onChange={(e) => setProblem(e.target.value)}
          placeholder="What problem does this solve? What is the current pain point?"
        />
      </div>

      <div style={{ marginBottom: "12px" }}>
        <label htmlFor="prop-evidence" style={labelStyle}>
          Evidence & Links *
        </label>
        <textarea
          id="prop-evidence"
          aria-label="Evidence"
          className="form-textarea"
          rows={2}
          style={inputStyle}
          value={evidence}
          onChange={(e) => setEvidence(e.target.value)}
          placeholder="Links to benchmarks, logs, issues, or profiling evidence."
        />
      </div>

      <div style={{ marginBottom: "12px" }}>
        <label htmlFor="prop-options" style={labelStyle}>
          Options Considered *
        </label>
        <textarea
          id="prop-options"
          aria-label="Options Considered"
          className="form-textarea"
          rows={2}
          style={inputStyle}
          value={optionsConsidered}
          onChange={(e) => setOptionsConsidered(e.target.value)}
          placeholder="What alternatives were evaluated? (comma-separated or lines)"
        />
      </div>

      <div
        className="fleet-cmd__form-grid"
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: "12px",
          marginBottom: "12px",
        }}
      >
        <div>
          <label htmlFor="prop-lean" style={labelStyle}>
            Submitter&apos;s Lean *
          </label>
          <input
            id="prop-lean"
            aria-label="Submitter's Lean"
            className="form-input"
            style={inputStyle}
            value={lean}
            onChange={(e) => setLean(e.target.value)}
            placeholder="e.g. Option B"
          />
        </div>

        <div>
          <label htmlFor="prop-cost" style={labelStyle}>
            Estimated Effort *
          </label>
          <select
            id="prop-cost"
            aria-label="Estimated Effort"
            className="form-select"
            style={inputStyle}
            value={estimatedCost}
            onChange={(e) => setEstimatedCost(e.target.value as ProposalEstimatedEffort)}
          >
            <option value="Low">Low</option>
            <option value="Medium">Medium</option>
            <option value="High">High</option>
          </select>
        </div>
      </div>

      <div
        className="fleet-cmd__form-grid"
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: "12px",
          marginBottom: "12px",
        }}
      >
        <div>
          <label htmlFor="prop-urgency" style={labelStyle}>
            Urgency *
          </label>
          <select
            id="prop-urgency"
            aria-label="Urgency"
            className="form-select"
            style={inputStyle}
            value={urgency}
            onChange={(e) => setUrgency(e.target.value as ProposalUrgency)}
          >
            <option value="Routine">Routine (next regular Friday Board meeting)</option>
            <option value="Urgent">Urgent (within 48 hours)</option>
            <option value="Emergency">Emergency (immediate / fleet blocker)</option>
          </select>
        </div>

        <div>
          <label htmlFor="prop-code-req" style={labelStyle}>
            Linked Code Request (optional)
          </label>
          <input
            id="prop-code-req"
            aria-label="Linked Code Request"
            className="form-input"
            style={inputStyle}
            value={codeRequestUrl}
            onChange={(e) => setCodeRequestUrl(e.target.value)}
            placeholder="URL to code request or feature request"
          />
        </div>
      </div>
    </PanelFrame>
  );
}
