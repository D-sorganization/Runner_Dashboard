/**
 * OperationsLocalToolsSubsection — Local Tools inside Operations (#1338).
 *
 * Collapsed by default. Opens when the URL hash is `#local-tools` (where retired
 * Local Tools tab routes and `/settings#local-tools` bookmarks land).
 */
import React, { useEffect, useState } from "react";
import { LocalAppsPage } from "../LocalApps";

export const LOCAL_TOOLS_SUBSECTION_ID = "local-tools";

function hashTargetsLocalTools(): boolean {
  if (typeof window === "undefined") return false;
  return window.location.hash.replace(/^#/, "") === LOCAL_TOOLS_SUBSECTION_ID;
}

export function OperationsLocalToolsSubsection(): React.ReactElement {
  const [open, setOpen] = useState<boolean>(hashTargetsLocalTools);

  useEffect(() => {
    const onHashChange = () => {
      if (hashTargetsLocalTools()) setOpen(true);
    };
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  return (
    <section
      id={LOCAL_TOOLS_SUBSECTION_ID}
      aria-label="Local Tools"
      style={{
        paddingTop: "0.75rem",
        marginBottom: "1rem",
        borderTop: "1px solid var(--border-subtle, #30363d)",
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: "1rem",
          marginBottom: open ? "0.75rem" : 0,
        }}
      >
        <div>
          <h3
            style={{
              fontSize: "1rem",
              fontWeight: 600,
              margin: "0 0 0.25rem 0",
              color: "var(--text-primary, #c9d1d9)",
            }}
          >
            Local Tools
          </h3>
          <p style={{ fontSize: "0.8125rem", color: "var(--text-muted, #8b949e)", margin: 0 }}>
            On-box daemons and local application processes the dashboard can start or probe.
          </p>
        </div>
        <button
          type="button"
          aria-expanded={open}
          aria-controls="operations-local-tools-body"
          onClick={() => setOpen((v) => !v)}
          style={{
            padding: "0.4rem 0.75rem",
            fontSize: "0.8125rem",
            fontWeight: 500,
            borderRadius: "6px",
            cursor: "pointer",
            background: "var(--bg-tertiary, #21262d)",
            color: "var(--text-secondary, #8b949e)",
            border: "1px solid var(--border-subtle, #30363d)",
          }}
        >
          {open ? "Hide local tools" : "Show local tools"}
        </button>
      </div>
      {open ? (
        <div id="operations-local-tools-body">
          <LocalAppsPage />
        </div>
      ) : null}
    </section>
  );
}

export default OperationsLocalToolsSubsection;
