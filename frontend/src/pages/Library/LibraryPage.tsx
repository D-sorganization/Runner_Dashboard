import React, { useCallback, useEffect, useState } from "react";
import { legacyFetch } from "../../lib/api";
import { EmptyState } from "../../primitives/EmptyState";

interface LibraryStats {
  works_active: number;
  files_present: number;
  passages: number;
  rejections: number;
  last_ingest: { finished_at: string | null; files_indexed: number } | null;
}

type BackendType = "google_drive_connector" | "google_drive_local" | "local_folder";
interface LibrarySource { id: string; label: string; root_path: string; shared: boolean; backend_type?: BackendType; drive_folder_id?: string; drive_folder_url?: string; preferential?: boolean; priority?: number; repos?: string[]; file_count?: number; last_ingest_at?: string | null; status: string; }
const BACKEND_LABELS: Record<BackendType, string> = { google_drive_connector: "Google Drive (Connector · P1)", google_drive_local: "Google Drive Desktop (Local · P2)", local_folder: "Local Folder (P3)" };
interface PooledSource { id: string; label: string; node: string; file_count: number; available: boolean; last_seen: string; }
interface SourcesData { sources: LibrarySource[]; pooled_sources?: PooledSource[]; unavailable_sources?: string[]; is_hub: boolean; current_node: string; nodes: { name: string; url: string }[]; }
interface BrowseData { current_path: string; parent_path: string | null; roots: string[]; entries: { name: string; path: string; child_dirs: number; file_count: number }[]; }

interface LibraryPassage {
  work_id: number;
  citation: string;
  text: string;
  score: number;
}

interface LibrarySearchResponse {
  passages: LibraryPassage[];
  tokens_used: number;
}

interface SubmissionRow {
  id: number;
  status: string;
  created_at: string;
  payload: { title: string; requesting_ref: string };
}

export function LibraryPage(): React.ReactElement {
  const [stats, setStats] = useState<LibraryStats | null>(null);
  const [sourcesData, setSourcesData] = useState<SourcesData | null>(null);
  const [submissions, setSubmissions] = useState<SubmissionRow[]>([]);
  const [query, setQuery] = useState("hip joint torque golf");
  const [results, setResults] = useState<LibrarySearchResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [pickerOpen, setPickerOpen] = useState(false);
  const [browseData, setBrowseData] = useState<BrowseData | null>(null);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [newLabel, setNewLabel] = useState("");
  const [newShared, setNewShared] = useState(false);
  const [newExcludes, setNewExcludes] = useState("");
  const [localBackendType, setLocalBackendType] = useState<BackendType>("local_folder");
  const [driveModalOpen, setDriveModalOpen] = useState(false);
  const [driveFolderInput, setDriveFolderInput] = useState("");
  const [driveLabel, setDriveLabel] = useState("");
  const [driveRepos, setDriveRepos] = useState("");
  const [driveShared, setDriveShared] = useState(true);
  const [drivePreferential, setDrivePreferential] = useState(true);
  const [removingSource, setRemovingSource] = useState<LibrarySource | null>(null);

  const loadMeta = useCallback(() => {
    Promise.all([legacyFetch("/api/v1/library/stats"), legacyFetch("/api/v1/library/submissions?status=pending"), legacyFetch("/api/v1/library/sources")])
      .then(async ([statsRes, subsRes, srcRes]) => {
        if (!statsRes.ok || !subsRes.ok || !srcRes.ok) throw new Error("failed to load library metadata");
        setStats((await statsRes.json()) as LibraryStats);
        setSubmissions(((await subsRes.json()) as { submissions: SubmissionRow[] }).submissions ?? []);
        setSourcesData((await srcRes.json()) as SourcesData);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  const loadBrowse = useCallback((path?: string) => {
    const query = path ? `?path=${encodeURIComponent(path)}` : "";
    legacyFetch("/api/v1/library/browse" + query)
      .then((r) => (r.ok ? (r.json() as Promise<BrowseData>) : Promise.reject(`browse ${r.status}`)))
      .then(setBrowseData)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  const patchSource = (id: string, patch: Record<string, unknown>) => {
    legacyFetch(`/api/v1/library/sources/${id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch) })
      .then((r) => r.json() as Promise<LibrarySource>)
      .then((up) => setSourcesData((p) => (p ? { ...p, sources: p.sources.map((i) => (i.id === up.id ? up : i)) } : null)))
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  };
  const toggleShare = (s: LibrarySource) => patchSource(s.id, { shared: !s.shared });
  const togglePreferential = (s: LibrarySource) => patchSource(s.id, { preferential: !s.preferential, priority: !s.preferential ? 1 : 2 });

  const confirmRemove = () => {
    if (!removingSource) return;
    legacyFetch(`/api/v1/library/sources/${removingSource.id}`, { method: "DELETE" })
      .then(() => {
        setSourcesData((p) => (p ? { ...p, sources: p.sources.filter((i) => i.id !== removingSource.id) } : null));
        setRemovingSource(null);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  };

  const submitAddSource = () => {
    if (!selectedPath || !newLabel.trim()) return;
    const slug = newLabel.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "source";
    legacyFetch("/api/v1/library/sources", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: slug, label: newLabel.trim(), root_path: selectedPath, backend_type: localBackendType, shared: newShared, exclude_globs: newExcludes.split(",").map((s) => s.trim()).filter(Boolean) }),
    })
      .then((r) => (r.ok ? (r.json() as Promise<LibrarySource>) : Promise.reject(`add source ${r.status}`)))
      .then((created) => {
        setSourcesData((p) => (p ? { ...p, sources: [...p.sources, created] } : null));
        setPickerOpen(false);
        setSelectedPath(null);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  };

  const submitAddDriveSource = () => {
    if (!driveFolderInput.trim() || !driveLabel.trim()) return;
    const slug = driveLabel.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "gdrive";
    const repos = driveRepos.split(",").map((s) => s.trim()).filter(Boolean);
    legacyFetch("/api/v1/library/sources", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: slug, label: driveLabel.trim(), backend_type: "google_drive_connector", drive_folder_url: driveFolderInput.trim(), shared: driveShared, preferential: drivePreferential, priority: drivePreferential ? 1 : 2, repos }),
    })
      .then((r) => (r.ok ? (r.json() as Promise<LibrarySource>) : Promise.reject(`add drive source ${r.status}`)))
      .then((created) => { setSourcesData((p) => (p ? { ...p, sources: [created, ...p.sources] } : null)); setDriveModalOpen(false); setDriveFolderInput(""); setDriveLabel(""); setDriveRepos(""); })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  };

  const runSearch = useCallback(() => {
    const params = new URLSearchParams({ q: query, budget: "1500" });
    legacyFetch(`/api/v1/library/search?${params}`)
      .then((r) => {
        if (!r.ok) throw new Error(`search ${r.status}`);
        return r.json() as Promise<LibrarySearchResponse>;
      })
      .then(setResults)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, [query]);

  useEffect(() => {
    loadMeta();
  }, [loadMeta]);

  if (error) {
    return (
      <div className="library-page">
        <p className="events-tab__degraded">Library unavailable: {error}</p>
      </div>
    );
  }

  return (
    <div className="library-page">
      <h1>Library (Alexandria)</h1>
      <p>Search indexed passages, ingest health, and the submission review queue (#2165).</p>

      <section>
        <h2>Ingest status</h2>
        {!stats ? (
          <p>Loading stats…</p>
        ) : (
          <ul>
            <li>Active works: {stats.works_active}</li>
            <li>Files present: {stats.files_present}</li>
            <li>Passages: {stats.passages}</li>
            <li>Rejected files: {stats.rejections}</li>
            <li>
              Last ingest:{" "}
              {stats.last_ingest?.finished_at
                ? `${stats.last_ingest.files_indexed} file(s) at ${stats.last_ingest.finished_at}`
                : "none recorded"}
            </li>
          </ul>
        )}
      </section>

      <section>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "8px" }}>
          <h2>Sources</h2>
          <div style={{ display: "flex", gap: "8px" }}>
            <button type="button" onClick={() => { setDriveModalOpen(true); setPickerOpen(false); }}>Add Google Drive</button>
            {!sourcesData?.is_hub && <button type="button" onClick={() => { setPickerOpen(true); setDriveModalOpen(false); setSelectedPath(null); loadBrowse(); }}>Add folder</button>}
          </div>
        </div>
        {sourcesData?.is_hub && (
          <div style={{ margin: "8px 0", padding: "8px 12px", background: "var(--bg-secondary)", borderRadius: 4 }}>
            <p style={{ margin: 0 }}>Hub view: sources are read-only. Manage each node's sources directly on its dashboard:</p>
            <div style={{ display: "flex", gap: "12px", marginTop: "6px" }}>
              {sourcesData.nodes.map((n) => (
                <a
                  key={n.name}
                  href={`${n.url}/library`}
                  target="_blank"
                  rel="noreferrer"
                  title={`Open library dashboard on ${n.name}`}
                  aria-label={`Open library on ${n.name}`}
                >
                  {n.name}
                </a>
              ))}
            </div>
          </div>
        )}
        {removingSource && (
          <div style={{ margin: "12px 0", padding: "12px", border: "1px solid var(--border-color)", borderRadius: 4 }}>
            <p><strong>Files will not be touched on disk.</strong> Remove source "{removingSource.label}"?</p>
            <button type="button" onClick={confirmRemove}>Confirm Remove</button> <button type="button" onClick={() => setRemovingSource(null)}>Cancel</button>
          </div>
        )}
        {driveModalOpen && (
          <div style={{ margin: "12px 0", padding: "12px", border: "1px solid var(--border-color)", borderRadius: 6, background: "var(--bg-secondary)" }}>
            <h3>Share Google Drive folder with agents (Preferential · Priority 1)</h3>
            <label style={{ display: "block", marginBottom: "6px" }}>Drive folder URL or ID: <input aria-label="Drive folder URL or ID" value={driveFolderInput} onChange={(e) => setDriveFolderInput(e.target.value)} placeholder="https://drive.google.com/drive/folders/..." style={{ marginLeft: "6px", width: "360px" }} /></label>
            <label style={{ display: "block", marginBottom: "6px" }}>Drive label: <input aria-label="Drive label" value={driveLabel} onChange={(e) => setDriveLabel(e.target.value)} placeholder="Agent Shared Library" style={{ marginLeft: "6px" }} /></label>
            <label style={{ display: "block", marginBottom: "6px" }}>Repo tags (optional): <input aria-label="Repo tags" value={driveRepos} onChange={(e) => setDriveRepos(e.target.value)} placeholder="Runner_Dashboard, AffineDrift" style={{ marginLeft: "6px" }} /></label>
            <label style={{ display: "block", marginBottom: "6px" }}><input type="checkbox" checked={driveShared} onChange={(e) => setDriveShared(e.target.checked)} /> Share with agents</label>
            <label style={{ display: "block", marginBottom: "8px" }}><input type="checkbox" checked={drivePreferential} onChange={(e) => setDrivePreferential(e.target.checked)} /> Use preferentially (Priority 1)</label>
            <button type="button" onClick={submitAddDriveSource}>Save Google Drive source</button> <button type="button" onClick={() => setDriveModalOpen(false)}>Cancel</button>
          </div>
        )}
        {pickerOpen && (
          <div style={{ margin: "12px 0", padding: "12px", border: "1px solid var(--border-color)", borderRadius: 6, background: "var(--bg-secondary)" }}>
            <h3>Choose folder on {sourcesData?.current_node || "node"}</h3>
            {browseData ? (
              <div>
                <div style={{ display: "flex", gap: "8px", alignItems: "center", marginBottom: "8px" }}>
                  <strong>Path:</strong> <code>{browseData.current_path}</code>
                  {browseData.parent_path && <button type="button" onClick={() => loadBrowse(browseData.parent_path!)}>Up</button>}
                  <button type="button" onClick={() => { setSelectedPath(browseData.current_path); setNewLabel(browseData.current_path.split(/[/\\]/).filter(Boolean).pop() || "source"); }}>Select this folder</button>
                </div>
                <div style={{ maxHeight: "200px", overflowY: "auto", border: "1px solid var(--border-subtle)", padding: "4px" }}>
                  {browseData.entries.length === 0 ? <p>No subdirectories</p> : browseData.entries.map((e) => (
                    <div key={e.path} onClick={() => loadBrowse(e.path)} style={{ padding: "4px 8px", cursor: "pointer" }}><strong>{e.name}</strong> ({e.file_count} files, {e.child_dirs} dirs)</div>
                  ))}
                </div>
                {selectedPath && (
                  <div style={{ marginTop: "12px", padding: "8px", borderTop: "1px solid var(--border-subtle)" }}>
                    <p>Selected: <code>{selectedPath}</code></p>
                    <label style={{ display: "block", marginBottom: "6px" }}>Backend option: <select aria-label="Backend option" value={localBackendType} onChange={(e) => setLocalBackendType(e.target.value as BackendType)} style={{ marginLeft: "6px" }}><option value="local_folder">Local folder (Priority 3 · Additional)</option><option value="google_drive_local">Google Drive Desktop mirror (Priority 2 · Additional)</option></select></label>
                    <label style={{ display: "block", marginBottom: "6px" }}>Source label: <input value={newLabel} onChange={(e) => setNewLabel(e.target.value)} style={{ marginLeft: "6px" }} /></label>
                    <label style={{ display: "block", marginBottom: "6px" }}><input type="checkbox" checked={newShared} onChange={(e) => setNewShared(e.target.checked)} /> Share with fleet</label>
                    <label style={{ display: "block", marginBottom: "8px" }}>Exclude patterns: <input value={newExcludes} onChange={(e) => setNewExcludes(e.target.value)} placeholder="*.tmp, private/*" style={{ marginLeft: "6px" }} /></label>
                    <button type="button" onClick={submitAddSource}>Add source</button> <button type="button" onClick={() => setPickerOpen(false)}>Cancel</button>
                  </div>
                )}
              </div>
            ) : <p>Loading folder browser…</p>}
          </div>
        )}
        {(!sourcesData?.sources || sourcesData.sources.length === 0) ? (
          <EmptyState title="No sources configured" description="Add a folder to index works into Alexandria." />
        ) : (
          <table className="data-table">
            <thead><tr><th>Label</th><th>Type</th><th>Folder ID / Path</th><th>Files</th><th>Last ingest</th><th>Preferential</th><th>Share</th>{!sourcesData.is_hub && <th>Action</th>}</tr></thead>
            <tbody>
              {sourcesData.sources.map((s) => {
                const btype: BackendType = s.backend_type || "local_folder";
                const canMutate = !sourcesData.is_hub || btype === "google_drive_connector";
                return (
                  <tr key={s.id}>
                    <td><strong>{s.label}</strong></td>
                    <td>{BACKEND_LABELS[btype]}</td>
                    <td><code>{s.drive_folder_id || s.root_path}</code></td>
                    <td>{btype === "google_drive_connector" ? "Drive MCP" : `${s.file_count ?? 0} files`}</td>
                    <td>{s.last_ingest_at ? new Date(s.last_ingest_at).toLocaleString() : "Never"}</td>
                    <td><label><input type="checkbox" checked={Boolean(s.preferential)} disabled={!canMutate} aria-label={`Preferential ${s.label}`} onChange={() => togglePreferential(s)} /> Preferential</label></td>
                    <td><label><input type="checkbox" checked={s.shared} disabled={!canMutate} aria-label={`Share ${s.label}`} onChange={() => toggleShare(s)} /> Share</label></td>
                    {!sourcesData.is_hub && <td><button type="button" onClick={() => setRemovingSource(s)}>Remove</button></td>}
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </section>

      <section>
        <h2>Fleet shared sources</h2>
        <p>Read-only catalog across registered nodes. Manage folders on their owning dashboard.</p>
        {!!sourcesData?.unavailable_sources?.length && <p role="status">Unavailable nodes: {sourcesData.unavailable_sources.join(", ")}. Cached entries may be out of date.</p>}
        {sourcesData?.pooled_sources?.length ? (
          <table className="data-table">
            <thead><tr><th>Source</th><th>Node</th><th>Files</th><th>Availability</th><th>Last seen</th></tr></thead>
            <tbody>{sourcesData.pooled_sources.map((source) => (
              <tr key={`${source.node}:${source.id}`}>
                <td>{source.label}</td><td>{source.node}</td><td>{source.file_count}</td>
                <td>{source.available ? "Online" : "Offline · cached"}</td>
                <td>{new Date(source.last_seen).toLocaleString()}</td>
              </tr>
            ))}</tbody>
          </table>
        ) : <p>No shared fleet sources discovered.</p>}
      </section>

      <section>
        <h2>Search</h2>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            runSearch();
          }}
        >
          <label>
            Query
            <input value={query} onChange={(e) => setQuery(e.target.value)} />
          </label>
          <button type="submit">Search</button>
        </form>
        {results && (
          <div>
            <p>{results.passages.length} passage(s), {results.tokens_used} tokens used</p>
            {results.passages.length === 0 ? (
              <EmptyState title="No hits" description="Try another query or check ingest status." />
            ) : (
              <ol>
                {results.passages.map((p) => (
                  <li key={`${p.work_id}-${p.citation}`}>
                    <strong>{p.citation}</strong> (score {p.score.toFixed(3)})
                    <p>{p.text.slice(0, 280)}{p.text.length > 280 ? "…" : ""}</p>
                  </li>
                ))}
              </ol>
            )}
          </div>
        )}
      </section>

      <section>
        <h2>Review queue ({submissions.length} pending)</h2>
        {submissions.length === 0 ? (
          <EmptyState title="Queue empty" description="New agent submissions appear here for Alexandria review." />
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Id</th>
                <th>Title</th>
                <th>Request</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {submissions.map((s) => (
                <tr key={s.id}>
                  <td>{s.id}</td>
                  <td>{s.payload.title}</td>
                  <td>{s.payload.requesting_ref}</td>
                  <td>{s.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section>
        <h2>Gap report</h2>
        <p>
          Weekly topic gaps and acquisition proposals ship with the Alexandria staff role (Repository_Management
          slice 5).
        </p>
      </section>
    </div>
  );
}

export default LibraryPage;
