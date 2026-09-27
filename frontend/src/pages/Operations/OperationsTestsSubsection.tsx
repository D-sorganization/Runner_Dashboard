/**
 * OperationsTestsSubsection — the Tests view inside Operations → Diagnostics
 * (#1338, owner decision: Tests is a diagnostic, not top-level nav).
 *
 * Collapsed by default, so opening Operations does not fetch the CI results
 * and heavy-test inventory. It opens on request, or when the URL hash is
 * `#tests` (where the retired `/settings/tests` and `/t/tests` routes land).
 */
import React, { useEffect, useState } from "react";
import { TestsPage } from "../TestsPage";

export const TESTS_SUBSECTION_ID = "tests";

function hashTargetsTests(): boolean {
  if (typeof window === "undefined") return false;
  return window.location.hash.replace(/^#/, "") === TESTS_SUBSECTION_ID;
}

export function OperationsTestsSubsection(): React.ReactElement {
  const [open, setOpen] = useState<boolean>(hashTargetsTests);

  useEffect(() => {
    const onHashChange = () => {
      if (hashTargetsTests()) setOpen(true);
    };
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  return (
    <div
      id={TESTS_SUBSECTION_ID}
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
            Tests
          </h3>
          <p style={{ fontSize: "0.8125rem", color: "var(--text-muted, #8b949e)", margin: 0 }}>
            Latest CI result per repository and the heavy test suite dispatch.
          </p>
        </div>
        <button
          type="button"
          aria-expanded={open}
          aria-controls="operations-tests-body"
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
          {open ? "Hide tests" : "Show tests"}
        </button>
      </div>
      {open ? (
        <div id="operations-tests-body">
          <TestsPage />
        </div>
      ) : null}
    </div>
  );
}

export default OperationsTestsSubsection;
