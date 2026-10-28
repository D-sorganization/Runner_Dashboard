/** Host settings — per-role and global default host preference (#3101). */
import { useCallback, useEffect, useState } from "react";
import { Dialog, DialogActions, DialogContent, DialogTitle } from "../../primitives/Dialog";
import { TouchButton } from "../../primitives/TouchButton";
import {
  fetchHostSettings,
  leaderLabel,
  previewRoleLeader,
  putHostSettings,
  type HostPreference,
  type HostSettingsPayload,
  type HostSettingsResponse,
} from "./hostSettingsApi";

const PREFERENCES: readonly HostPreference[] = ["grokbot-first", "rd-first", "both"];

export function HostSettingsPanel() {
  const [data, setData] = useState<HostSettingsResponse | null>(null);
  const [draft, setDraft] = useState<HostSettingsPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);

  const load = useCallback((signal?: AbortSignal) => {
    fetchHostSettings(signal)
      .then((res) => {
        setData(res);
        setDraft(res.settings);
        setError(null);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  useEffect(() => {
    const c = new AbortController();
    load(c.signal);
    return () => c.abort();
  }, [load]);

  if (!draft || !data) {
    return (
      <div data-testid="host-settings-loading">
        <h3>Host settings</h3>
        {error ? <p role="alert">{error}</p> : <p>Loading…</p>}
      </div>
    );
  }

  const dirty = JSON.stringify(draft) !== JSON.stringify(data.settings);
  const roles = data.resolved.map((r) => r.role);

  const save = () => {
    setSaving(true);
    putHostSettings(draft, true)
      .then((res) => {
        setData(res);
        setDraft(res.settings);
        setConfirmOpen(false);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setSaving(false));
  };

  return (
    <div data-testid="host-settings-panel">
      <h3>Host settings</h3>
      <label>
        Global preference
        <select
          value={draft.global.preference}
          onChange={(e) => setDraft({ ...draft, global: { ...draft.global, preference: e.target.value as HostPreference } })}
        >
          {PREFERENCES.map((p) => (
            <option key={p} value={p}>{p}</option>
          ))}
        </select>
      </label>
      <label>
        <input
          type="checkbox"
          checked={draft.global.hosts.grokbot.enabled}
          onChange={(e) =>
            setDraft({
              ...draft,
              global: { ...draft.global, hosts: { ...draft.global.hosts, grokbot: { enabled: e.target.checked } } },
            })
          }
        />
        Grok Bot on
      </label>
      <label>
        <input
          type="checkbox"
          checked={draft.global.hosts.rd.enabled}
          onChange={(e) =>
            setDraft({
              ...draft,
              global: { ...draft.global, hosts: { ...draft.global.hosts, rd: { enabled: e.target.checked } } },
            })
          }
        />
        RD on
      </label>
      <table>
        <thead>
          <tr>
            <th>Role</th>
            <th>Override</th>
            <th>Leader</th>
          </tr>
        </thead>
        <tbody>
          {roles.map((role) => (
            <tr key={role} data-testid={`host-settings-row-${role}`}>
              <td>{role}</td>
              <td>
                <select
                  aria-label={`Preference for ${role}`}
                  value={draft.roles[role]?.preference ?? ""}
                  onChange={(e) => {
                    const v = e.target.value as HostPreference | "";
                    const rolesMap = { ...draft.roles };
                    if (!v) delete rolesMap[role];
                    else rolesMap[role] = { ...(rolesMap[role] ?? draft.global), preference: v };
                    setDraft({ ...draft, roles: rolesMap });
                  }}
                >
                  <option value="">(global)</option>
                  {PREFERENCES.map((p) => (
                    <option key={p} value={p}>{p}</option>
                  ))}
                </select>
              </td>
              <td data-testid={`host-settings-leader-${role}`}>
                {leaderLabel(previewRoleLeader(role, draft))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <TouchButton disabled={!dirty || saving} onClick={() => setConfirmOpen(true)} data-testid="host-settings-save">
        Save
      </TouchButton>
      {error ? <p role="alert">{error}</p> : null}
      <Dialog open={confirmOpen} onClose={() => !saving && setConfirmOpen(false)}>
        <DialogTitle>Confirm host settings</DialogTitle>
        <DialogContent>
          <ul>
            {roles.map((role) => (
              <li key={role}>{role}: {leaderLabel(previewRoleLeader(role, draft))}</li>
            ))}
          </ul>
        </DialogContent>
        <DialogActions>
          <TouchButton onClick={() => setConfirmOpen(false)} disabled={saving}>Cancel</TouchButton>
          <TouchButton onClick={save} disabled={saving} data-testid="host-settings-confirm">Confirm</TouchButton>
        </DialogActions>
      </Dialog>
    </div>
  );
}
