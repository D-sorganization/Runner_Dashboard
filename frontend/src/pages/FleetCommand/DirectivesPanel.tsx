/**
 * DirectivesPanel.tsx — operator directives editor (#1233).
 *
 * Reads `GET /api/priorities/directives` (active only, priority 1 first) and
 * saves with `PUT /api/priorities/directives`, which replaces the whole list
 * (same edit-then-save model as the Staff Holds editor). "Expire now" drops a
 * directive from the list on the next save; an expiry date lapses it later.
 * The PUT goes through `apiRequest`, so it carries the CSRF header.
 */
import { useEffect, useState } from "react";
import { Badge } from "../../primitives/Badge";
import { TouchButton } from "../../primitives/TouchButton";
import { describeError, expiryLabel, fetchDirectives, putDirectives, useResource } from "./fleetApi";
import { PanelFrame } from "./PanelFrame";
import type { Directive } from "./types";

const PRIORITIES = [1, 2, 3, 4, 5] as const;

interface Draft extends Directive {
  /** Client-only row key; `id` is absent for rows not yet saved. */
  key: string;
}

let draftSeq = 0;
const nextKey = () => `draft-${(draftSeq += 1)}`;

function toDraft(d: Directive): Draft {
  return { ...d, key: d.id ?? nextKey() };
}

/** `YYYY-MM-DD` from a stored ISO expiry, for the date input. */
function expiryDate(expires: string | null | undefined): string {
  return expires ? expires.slice(0, 10) : "";
}

function toBody(d: Draft): Directive {
  return {
    ...(d.id ? { id: d.id } : {}),
    text: d.text.trim(),
    repo: d.repo.trim() || "*",
    priority: d.priority,
    expires: d.expires || null,
    ...(d.set_by ? { set_by: d.set_by } : {}),
    ...(d.set_on ? { set_on: d.set_on } : {}),
  };
}

export function DirectivesPanel() {
  const res = useResource(fetchDirectives, "directives");
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (res.data) {
      setDrafts(res.data.directives.map(toDraft));
      setDirty(false);
    }
  }, [res.data]);

  const update = (key: string, patch: Partial<Draft>) => {
    setDrafts((prev) => prev.map((d) => (d.key === key ? { ...d, ...patch } : d)));
    setDirty(true);
    setNotice(null);
  };

  const add = () => {
    setDrafts((prev) => [...prev, { key: nextKey(), text: "", repo: "*", priority: 3, expires: null }]);
    setDirty(true);
    setNotice(null);
  };

  const expire = (key: string) => {
    setDrafts((prev) => prev.filter((d) => d.key !== key));
    setDirty(true);
    setNotice(null);
  };

  const invalid = drafts.some((d) => !d.text.trim());

  const save = () => {
    setSaving(true);
    setSaveError(null);
    putDirectives(drafts.map(toBody))
      .then((data) => {
        setDrafts(data.directives.map(toDraft));
        setDirty(false);
        setNotice(`Saved ${data.directives.length} active directive${data.directives.length === 1 ? "" : "s"}.`);
      })
      .catch((e: unknown) => setSaveError(describeError(e)))
      .finally(() => setSaving(false));
  };

  return (
    <PanelFrame
      title="Directives"
      testId="fleet-directives"
      loading={res.loading}
      hasData={res.data !== null}
      error={res.error}
      unavailable={res.unavailable}
      onRetry={res.reload}
      actions={
        <>
          <TouchButton onClick={add}>Add directive</TouchButton>
          <TouchButton variant="primary" onClick={save} disabled={!dirty || saving || invalid}>
            {saving ? "Saving..." : "Save"}
          </TouchButton>
        </>
      }
    >
      <p className="staff-muted">
        Standing operator guidance every agent sees in its briefing. Priority 1 is the most urgent; repo <code>*</code>{" "}
        applies fleet-wide.
      </p>
      {saveError ? (
        <p className="staff-error" role="alert">
          {saveError}
        </p>
      ) : null}
      {notice ? (
        <p className="fleet-cmd__notice" role="status">
          {notice}
        </p>
      ) : null}
      {drafts.length === 0 ? (
        <p className="staff-muted" data-testid="directives-empty">
          No active directives.
        </p>
      ) : (
        <ul className="fleet-cmd__directives">
          {drafts.map((d, i) => {
            const label = `Directive ${i + 1}`;
            return (
              <li key={d.key} className="fleet-cmd__directive" data-testid={`directive-${i}`}>
                <div className="fleet-cmd__row">
                  <Badge tone={d.priority <= 2 ? "danger" : d.priority === 3 ? "warning" : "neutral"} size="sm">
                    P{d.priority}
                  </Badge>
                  <input
                    className="form-input fleet-cmd__grow"
                    aria-label={`${label} text`}
                    value={d.text}
                    maxLength={500}
                    placeholder="What the fleet should (or should not) do"
                    onChange={(e) => update(d.key, { text: e.target.value })}
                  />
                  <TouchButton
                    variant="danger"
                    onClick={() => expire(d.key)}
                    aria-label={`Expire ${label.toLowerCase()}`}
                  >
                    Expire now
                  </TouchButton>
                </div>
                <div className="fleet-cmd__row">
                  <label className="form-label">
                    Priority{" "}
                    <select
                      className="form-select"
                      aria-label={`${label} priority`}
                      value={d.priority}
                      onChange={(e) => update(d.key, { priority: Number(e.target.value) })}
                    >
                      {PRIORITIES.map((p) => (
                        <option key={p} value={p}>
                          {p}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="form-label">
                    Repo{" "}
                    <input
                      className="form-input fleet-cmd__short"
                      aria-label={`${label} repo`}
                      value={d.repo}
                      placeholder="*"
                      onChange={(e) => update(d.key, { repo: e.target.value })}
                    />
                  </label>
                  <label className="form-label">
                    Expires{" "}
                    <input
                      type="date"
                      className="form-input"
                      aria-label={`${label} expiry`}
                      value={expiryDate(d.expires)}
                      onChange={(e) =>
                        update(d.key, { expires: e.target.value ? `${e.target.value}T23:59:59Z` : null })
                      }
                    />
                  </label>
                  <span className="staff-muted">
                    {expiryLabel(d.expires, "lapses") || "no expiry"}
                    {d.set_by ? ` · set by ${d.set_by}` : ""}
                  </span>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </PanelFrame>
  );
}

export default DirectivesPanel;
