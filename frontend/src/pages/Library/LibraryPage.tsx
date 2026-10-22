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
  const [submissions, setSubmissions] = useState<SubmissionRow[]>([]);
  const [query, setQuery] = useState("hip joint torque golf");
  const [results, setResults] = useState<LibrarySearchResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadMeta = useCallback(() => {
    Promise.all([
      legacyFetch("/api/v1/library/stats"),
      legacyFetch("/api/v1/library/submissions?status=pending"),
    ])
      .then(async ([statsRes, subsRes]) => {
        if (!statsRes.ok) throw new Error(`stats ${statsRes.status}`);
        if (!subsRes.ok) throw new Error(`submissions ${subsRes.status}`);
        const statsJson = (await statsRes.json()) as LibraryStats;
        const subsJson = (await subsRes.json()) as { submissions: SubmissionRow[] };
        setStats(statsJson);
        setSubmissions(subsJson.submissions ?? []);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

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
