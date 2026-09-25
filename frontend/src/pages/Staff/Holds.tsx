/**
 * Holds.tsx — standing holds that stop roles from running (#1198).
 *
 * Reads `GET /api/staff/holds` and writes `PUT /api/staff/holds` with the
 * shape `{holds: [{id, text, set_on, lifted_when, applies_to, active}]}`
 * agreed with the parallel scheduler/holds PR (#1196). Until that lands the
 * route answers 404, which this panel renders as an explicit "holds
 * unavailable" state rather than an error (orthogonality: the rest of the
 * tab keeps working).
 */
import { useCallback, useEffect, useState } from "react";
import { Badge } from "../../primitives/Badge";
import { EmptyState } from "../../primitives/EmptyState";
import { TouchButton } from "../../primitives/TouchButton";
import { errorMessage, fetchHolds, isNotFound, putHolds, type Hold } from "./staffApi";
import {
  invalidateStaffQueries,
  useResolvedQueryClient,
} from "../../hooks/useStaffQueries";

export interface HoldsProps {
  /** Role names offered in the applies-to picker (from the roster). */
  roles: string[];
}

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

function newHoldId(): string {
  return `hold-${Date.now().toString(36)}`;
}

export function Holds({ roles }: HoldsProps) {
  const client = useResolvedQueryClient();
  const [holds, setHolds] = useState<Hold[] | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);

  const load = useCallback((signal?: AbortSignal) => {
    setError(null);
    fetchHolds(signal)
      .then((data) => {
        setHolds(data.holds);
        setUnavailable(false);
        setDirty(false);
      })
      .catch((e: unknown) => {
        if (signal?.aborted) return;
        if (isNotFound(e)) {
          setUnavailable(true);
          setHolds(null);
          return;
        }
        setError(errorMessage(e));
      });
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal);
    return () => controller.abort();
  }, [load]);

  const update = (id: string, patch: Partial<Hold>) => {
    setHolds((prev) => (prev ?? []).map((h) => (h.id === id ? { ...h, ...patch } : h)));
    setDirty(true);
  };

  const add = () => {
    setHolds((prev) => [
      ...(prev ?? []),
      { id: newHoldId(), text: "", set_on: todayIso(), lifted_when: "", applies_to: [], active: true },
    ]);
    setDirty(true);
  };

  const remove = (id: string) => {
    setHolds((prev) => (prev ?? []).filter((h) => h.id !== id));
    setDirty(true);
  };

  const save = () => {
    if (!holds) return;
    setSaving(true);
    setError(null);
    putHolds({ holds })
      .then((data) => {
        setHolds(data.holds);
        setDirty(false);
        setSaving(false);
        invalidateStaffQueries(client);
      })
      .catch((e: unknown) => {
        setError(errorMessage(e));
        setSaving(false);
      });
  };

  if (unavailable) {
    return (
      <div className="glass-card staff-panel" data-testid="holds-unavailable">
        <EmptyState
          title="Holds unavailable"
          description="This node's backend does not serve /api/staff/holds yet (scheduler, windows, holds and budgets land with #1196)."
          onRetry={() => load()}
        />
      </div>
    );
  }
  if (error && !holds) {
    return (
      <div className="glass-card staff-panel">
        <EmptyState variant="error" title="Failed to load holds" description={error} onRetry={() => load()} />
      </div>
    );
  }
  if (!holds) {
    return (
      <div className="glass-card staff-panel" aria-busy="true">
        <p className="staff-muted">Loading holds...</p>
      </div>
    );
  }

  return (
    <div className="glass-card staff-panel staff-holds">
      <div className="staff-panel__header">
        <h3 className="staff-panel__title">Holds</h3>
        <div className="staff-holds__actions">
          <TouchButton onClick={add}>Add hold</TouchButton>
          <TouchButton variant="primary" onClick={save} disabled={!dirty || saving}>
            {saving ? "Saving..." : "Save"}
          </TouchButton>
        </div>
      </div>
      {error ? <p className="staff-error">{error}</p> : null}
      {holds.length === 0 ? (
        <p className="staff-muted">No holds. Roles run whenever their schedule or an operator says so.</p>
      ) : (
        <ul className="staff-holds__list">
          {holds.map((hold) => (
            <li key={hold.id} className="staff-hold" data-testid={`hold-${hold.id}`}>
              <div className="staff-hold__row">
                <label className="staff-hold__active">
                  <input
                    type="checkbox"
                    checked={hold.active}
                    onChange={(e) => update(hold.id, { active: e.target.checked })}
                  />
                  <Badge tone={hold.active ? "danger" : "neutral"} size="sm">
                    {hold.active ? "active" : "lifted"}
                  </Badge>
                </label>
                <input
                  className="form-input staff-hold__text"
                  aria-label={`Hold ${hold.id} text`}
                  value={hold.text}
                  placeholder="What is on hold and why"
                  onChange={(e) => update(hold.id, { text: e.target.value })}
                />
                <TouchButton variant="danger" onClick={() => remove(hold.id)} aria-label={`Remove hold ${hold.id}`}>
                  Remove
                </TouchButton>
              </div>
              <div className="staff-hold__row">
                <label className="form-label" htmlFor={`hold-set-${hold.id}`}>
                  Set on
                </label>
                <input
                  id={`hold-set-${hold.id}`}
                  className="form-input staff-hold__date"
                  value={hold.set_on}
                  onChange={(e) => update(hold.id, { set_on: e.target.value })}
                />
                <label className="form-label" htmlFor={`hold-lift-${hold.id}`}>
                  Lifted when
                </label>
                <input
                  id={`hold-lift-${hold.id}`}
                  className="form-input"
                  value={hold.lifted_when}
                  placeholder="condition or date"
                  onChange={(e) => update(hold.id, { lifted_when: e.target.value })}
                />
              </div>
              <div className="staff-hold__row">
                <span className="form-label">Applies to</span>
                <div className="staff-hold__roles">
                  {roles.length === 0 ? (
                    <span className="staff-muted">all roles</span>
                  ) : (
                    roles.map((name) => {
                      const checked = hold.applies_to.includes(name);
                      return (
                        <label key={name} className="staff-hold__role">
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={(e) =>
                              update(hold.id, {
                                applies_to: e.target.checked
                                  ? [...hold.applies_to, name]
                                  : hold.applies_to.filter((r) => r !== name),
                              })
                            }
                          />
                          {name}
                        </label>
                      );
                    })
                  )}
                  {hold.applies_to.length === 0 ? <span className="staff-muted">(all roles)</span> : null}
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default Holds;
