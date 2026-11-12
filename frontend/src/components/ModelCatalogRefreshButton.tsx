/**
 * ModelCatalogRefreshButton — On-demand POST /api/models/refresh control and cache age badge (#3533).
 */
import { useState } from "react"

export interface ModelCatalogSnapshot {
  providers: Record<string, string[]>
  refreshed_at?: number | null
  age_seconds?: number | null
  stale?: boolean
}

export function ModelCatalogRefreshButton({
  snapshot,
  onRefreshed,
  fetchImpl = fetch,
}: {
  snapshot?: ModelCatalogSnapshot | null
  onRefreshed?: (next: ModelCatalogSnapshot) => void
  fetchImpl?: typeof fetch
}) {
  const [current, setCurrent] = useState<ModelCatalogSnapshot | null>(snapshot ?? null)
  const [busy, setBusy] = useState(false)
  const active = current ?? snapshot ?? null
  const age = active?.age_seconds

  const handleRefresh = async () => {
    setBusy(true)
    try {
      const resp = await fetchImpl("/api/models/refresh", {
        method: "POST",
        headers: { "X-Requested-With": "XMLHttpRequest" },
      })
      if (resp.ok) {
        const data = (await resp.json()) as ModelCatalogSnapshot
        setCurrent(data)
        onRefreshed?.(data)
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="model-catalog-refresh" data-testid="model-catalog-refresh">
      <button type="button" onClick={() => void handleRefresh()} disabled={busy} data-testid="model-catalog-refresh-btn">
        {busy ? "Refreshing…" : "Refresh models"}
      </button>
      {typeof age === "number" && (
        <span data-testid="model-catalog-age">
          {active?.stale ? `Stale cache (${Math.round(age)}s old)` : `Cache age: ${Math.round(age)}s`}
        </span>
      )}
    </div>
  )
}
