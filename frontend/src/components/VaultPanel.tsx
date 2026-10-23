import { useCallback, useEffect, useState } from 'react';
import { apiFetch } from '../api/client';

interface VaultSecretRecord {
  key: string;
  scope: string;
  description: string;
  handle: string;
  masked_value: string;
  fingerprint: string;
  updated_at: string;
}

export function VaultPanel() {
  const [secrets, setSecrets] = useState<VaultSecretRecord[]>([]);
  const [keyName, setKeyName] = useState('');
  const [secretValue, setSecretValue] = useState('');
  const [scope, setScope] = useState('global');
  const [status, setStatus] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await apiFetch('/api/v1/vault/secrets');
      if (res.ok) {
        const data = await res.json();
        setSecrets(Array.isArray(data.secrets) ? data.secrets : []);
      }
    } catch { /* ignore */ }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!keyName.trim() || !secretValue) return;
    const res = await apiFetch('/api/v1/vault/secrets', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key: keyName.trim(), value: secretValue, scope, description: '' }),
    });
    if (res.ok) {
      setKeyName('');
      setSecretValue('');
      setStatus('Stored encrypted secret handle.');
      await load();
    } else {
      setStatus('Failed to store secret.');
    }
  };

  return (
    <div className="panel" data-testid="vault-panel">
      <div className="panel-body">
        <p className="muted" style={{ marginTop: 0 }}>
          AES-256-GCM encrypted-at-rest secret store keyed by strict <code>0600</code> master key. Plaintext values are write-only; reference handles via <code>$vault:&lt;KEY&gt;</code>.
        </p>
        <form onSubmit={handleSave} style={{ display: 'grid', gap: 8, gridTemplateColumns: '1fr 1fr 140px auto', marginBottom: 12 }}>
          <input aria-label="Vault key" placeholder="KEY_NAME (e.g. PROD_API_TOKEN)" value={keyName} onChange={(e) => setKeyName(e.target.value)} />
          <input aria-label="Secret value" type="password" placeholder="Write-only secret value" value={secretValue} onChange={(e) => setSecretValue(e.target.value)} />
          <input aria-label="Vault scope" placeholder="global or repo:Name" value={scope} onChange={(e) => setScope(e.target.value)} />
          <button type="submit" className="btn btn-primary">Store Secret</button>
        </form>
        {status && <div className="muted" role="status" style={{ marginBottom: 8 }}>{status}</div>}
        <table className="table" style={{ width: '100%' }}>
          <thead><tr><th>Key</th><th>Handle</th><th>Scope</th><th>Masked</th><th>Fingerprint</th><th /></tr></thead>
          <tbody>
            {secrets.map((s) => (
              <tr key={s.key}>
                <td><code>{s.key}</code></td><td><code>{s.handle}</code></td><td>{s.scope}</td><td>{s.masked_value}</td><td><code>{s.fingerprint}</code></td>
                <td><button type="button" className="btn btn-sm" onClick={() => void apiFetch(`/api/v1/vault/secrets/${encodeURIComponent(s.key)}`, { method: 'DELETE' }).then(load)}>Delete</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
