/**
 * NewPanelForm.tsx — Form for starting a new expert panel (SC-D/Issue #1635).
 *
 * Implements topic, mode, rounds (1-6), 3-4 expert rows with preset picker,
 * inline validation, and cost-guard confirmation with re-submission.
 */
import React, { useEffect, useState } from "react";
import { TouchButton } from "../../primitives/TouchButton";
import { GroupCostConfirm } from "./GroupCostConfirm";
import {
  createPanel,
  fetchPanelPresets,
  PanelCostRequired,
  type PanelCostEstimate,
  type PanelPreset,
} from "./panelApi";
import { validatePanelForm, type PanelFormExpert, type PanelFormState } from "./panelTurn";
import type { ThreadInfo } from "./threadTypes";
import "./panel.css";

export interface NewPanelFormProps {
  onCreated: (thread: ThreadInfo) => void;
  onCancel?: () => void;
}

const DEFAULT_PROVIDERS = ["claude", "codex", "gemini"];

const INITIAL_EXPERTS: PanelFormExpert[] = [
  { name: "", perspective: "", provider: "claude", model: "" },
  { name: "", perspective: "", provider: "claude", model: "" },
  { name: "", perspective: "", provider: "claude", model: "" },
];

export function NewPanelForm({ onCreated, onCancel }: NewPanelFormProps) {
  const [topic, setTopic] = useState("");
  const [mode, setMode] = useState<"debate" | "brainstorm">("debate");
  const [rounds, setRounds] = useState(3);
  const [experts, setExperts] = useState<PanelFormExpert[]>(INITIAL_EXPERTS);

  const [presets, setPresets] = useState<PanelPreset[]>([]);
  const [providers, setProviders] = useState<string[]>(DEFAULT_PROVIDERS);
  const [selectedPresetId, setSelectedPresetId] = useState("");

  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [costError, setCostError] = useState<{ estimate: PanelCostEstimate | null; message: string } | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchPanelPresets()
      .then((data) => {
        if (!cancelled) {
          setPresets(data.presets || []);
          if (data.providers && data.providers.length > 0) {
            setProviders(data.providers);
          }
        }
      })
      .catch(() => {
        // fail-open with defaults
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const formState: PanelFormState = {
    topic,
    mode,
    rounds,
    experts,
  };

  const validationErrors = validatePanelForm(formState);

  const handleSelectPreset = (presetId: string) => {
    setSelectedPresetId(presetId);
    if (!presetId) return;
    const found = presets.find((p) => p.id === presetId);
    if (!found) return;

    const defaultProvider = providers[0] || "claude";
    const mapped: PanelFormExpert[] = found.experts.map((e) => ({
      name: e.name,
      perspective: e.perspective,
      provider: defaultProvider,
      model: "",
    }));
    setExperts(mapped);
  };

  const handleUpdateExpert = (index: number, patch: Partial<PanelFormExpert>) => {
    setExperts((prev) => {
      const next = [...prev];
      next[index] = { ...next[index], ...patch };
      return next;
    });
  };

  const handleAddExpert = () => {
    if (experts.length >= 4) return;
    const defaultProvider = providers[0] || "claude";
    setExperts((prev) => [...prev, { name: "", perspective: "", provider: defaultProvider, model: "" }]);
  };

  const handleRemoveExpert = (index: number) => {
    if (experts.length <= 3) return;
    setExperts((prev) => prev.filter((_, i) => i !== index));
  };

  const handleSubmit = async (confirmCost: boolean = false) => {
    if (validationErrors.length > 0) return;
    setIsSubmitting(true);
    setSubmitError(null);

    const payload = {
      topic: topic.trim(),
      mode,
      rounds,
      confirm_cost: confirmCost,
      moderator_provider: providers[0] || "claude",
      experts: experts.map((e) => ({
        name: e.name.trim(),
        perspective: e.perspective.trim(),
        provider: e.provider || providers[0] || "claude",
        model: e.model?.trim() || null,
      })),
    };

    try {
      const result = await createPanel(payload);
      setCostError(null);
      onCreated(result.thread);
    } catch (err: unknown) {
      if (err instanceof PanelCostRequired) {
        setCostError({ estimate: err.estimate ?? null, message: err.message });
      } else {
        setCostError(null);
        setSubmitError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <form
      className="panel-form"
      aria-label="New expert panel"
      onSubmit={(e) => {
        e.preventDefault();
        void handleSubmit(false);
      }}
    >
      <header>
        <h3 style={{ margin: "0 0 4px 0", fontSize: 16 }}>Start an Expert Panel</h3>
        <p style={{ margin: 0, fontSize: 12, color: "var(--text-muted, #8b949e)" }}>
          Assemble 3 to 4 specialized agents to debate or brainstorm a complex question.
        </p>
      </header>

      {/* Preset Picker */}
      <div className="panel-form__field">
        <label className="panel-form__label" htmlFor="panel-preset-select">
          Choose a preset (optional)
        </label>
        <select
          id="panel-preset-select"
          aria-label="Preset"
          className="panel-form__select"
          value={selectedPresetId}
          onChange={(e) => handleSelectPreset(e.target.value)}
        >
          <option value="">Custom panel line-up…</option>
          {presets.map((preset) => (
            <option key={preset.id} value={preset.id}>
              {preset.title}
            </option>
          ))}
        </select>
      </div>

      {/* Topic Textarea */}
      <div className="panel-form__field">
        <label className="panel-form__label" htmlFor="panel-topic-input">
          Topic / Deliberation Prompt
        </label>
        <textarea
          id="panel-topic-input"
          aria-label="Topic"
          className="panel-form__textarea"
          rows={3}
          placeholder="State the technical question or architectural decision for the panel to debate…"
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
        />
      </div>

      {/* Mode & Rounds */}
      <div className="panel-form__row">
        <div className="panel-form__field" style={{ flex: 1 }}>
          <label className="panel-form__label" htmlFor="panel-mode-select">
            Mode
          </label>
          <select
            id="panel-mode-select"
            aria-label="Mode"
            className="panel-form__select"
            value={mode}
            onChange={(e) => setMode(e.target.value as "debate" | "brainstorm")}
          >
            <option value="debate">Debate (stops early on consensus)</option>
            <option value="brainstorm">Brainstorm (runs all rounds)</option>
          </select>
        </div>

        <div className="panel-form__field" style={{ width: 100 }}>
          <label className="panel-form__label" htmlFor="panel-rounds-input">
            Rounds (1–6)
          </label>
          <input
            id="panel-rounds-input"
            type="number"
            min={1}
            max={6}
            aria-label="Rounds"
            className="panel-form__input"
            value={rounds}
            onChange={(e) => setRounds(Number(e.target.value))}
          />
        </div>
      </div>

      {/* Experts Section */}
      <div className="panel-form__experts">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span className="panel-form__label">Experts ({experts.length} of 3–4)</span>
          <TouchButton
            type="button"
            aria-label="Add expert"
            onClick={handleAddExpert}
            disabled={experts.length >= 4}
          >
            + Add expert
          </TouchButton>
        </div>

        {experts.map((exp, idx) => (
          <div key={idx} className="panel-form__expert-row">
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <strong style={{ fontSize: 12 }}>Expert #{idx + 1}</strong>
              {experts.length > 3 && (
                <TouchButton
                  type="button"
                  aria-label="Remove expert"
                  onClick={() => handleRemoveExpert(idx)}
                >
                  Remove
                </TouchButton>
              )}
            </div>

            <div className="panel-form__expert-inputs">
              <input
                type="text"
                aria-label="Expert name"
                placeholder="Name (e.g. Architect)"
                className="panel-form__input"
                value={exp.name}
                onChange={(e) => handleUpdateExpert(idx, { name: e.target.value })}
              />

              <select
                aria-label="Expert provider"
                className="panel-form__select"
                value={exp.provider || providers[0] || "claude"}
                onChange={(e) => handleUpdateExpert(idx, { provider: e.target.value })}
              >
                {providers.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </div>

            <textarea
              rows={2}
              aria-label="Expert perspective"
              placeholder="Perspective (e.g. Failure modes, hidden assumptions, maintainability)"
              className="panel-form__textarea"
              value={exp.perspective}
              onChange={(e) => handleUpdateExpert(idx, { perspective: e.target.value })}
            />

            <input
              type="text"
              aria-label="Expert model"
              placeholder="Model (optional)"
              className="panel-form__input"
              value={exp.model ?? ""}
              onChange={(e) => handleUpdateExpert(idx, { model: e.target.value })}
            />
          </div>
        ))}
      </div>

      {/* Inline Validation Errors */}
      {validationErrors.length > 0 && (
        <div role="alert" className="panel-form__errors" aria-label="Validation errors">
          <ul>
            {validationErrors.map((err, i) => (
              <li key={i}>{err}</li>
            ))}
          </ul>
        </div>
      )}

      {/* General Submit Error (e.g. 429) */}
      {submitError && (
        <div role="alert" className="panel-form__submit-error">
          {submitError}
        </div>
      )}

      {/* Cost Confirmation */}
      {costError && (
        <GroupCostConfirm
          estimate={costError.estimate}
          message={costError.message}
          label="Panel cost confirmation"
          titlePrefix="Running this panel is estimated at"
          confirmLabel="Confirm"
          cancelLabel="Cancel"
          onConfirm={() => void handleSubmit(true)}
          onCancel={() => setCostError(null)}
        />
      )}

      {/* Actions */}
      <div className="panel-form__actions">
        {onCancel && (
          <TouchButton type="button" onClick={onCancel} disabled={isSubmitting}>
            Cancel
          </TouchButton>
        )}
        <TouchButton
          type="submit"
          variant="primary"
          aria-label="Start panel"
          disabled={validationErrors.length > 0 || isSubmitting}
        >
          {isSubmitting ? "Starting panel…" : "Start panel"}
        </TouchButton>
      </div>
    </form>
  );
}
