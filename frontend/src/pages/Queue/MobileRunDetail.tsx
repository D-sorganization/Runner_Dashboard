import { BottomSheet } from "../../primitives/BottomSheet";
import { TouchButton } from "../../primitives/TouchButton";
import type { RunDetail } from "./mobileTypes";
import { runnerName, statusLabel, timingLabel, triggeredBy } from "./mobileTypes";

interface MobileRunDetailProps {
  selectedRun: RunDetail | null;
  onClose: () => void;
  onRerun: (detail: RunDetail) => void;
  onCancel: (detail: RunDetail) => void;
  cancelling: Record<string, boolean>;
  cancelDone: Record<string, boolean>;
}

export function MobileRunDetail({
  selectedRun,
  onClose,
  onRerun,
  onCancel,
  cancelling,
  cancelDone,
}: MobileRunDetailProps) {
  const selectedKey = selectedRun
    ? `${selectedRun.repo}/${selectedRun.run.id}`
    : null;

  return (
    <BottomSheet
      isOpen={!!selectedRun}
      onClose={onClose}
      title={selectedRun?.run.name ?? "Workflow run"}
    >
      {selectedRun && (
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          {/* Meta rows */}
          <div
            style={{
              background: "var(--bg-tertiary)",
              borderRadius: 8,
              display: "flex",
              flexDirection: "column",
              gap: 8,
              padding: "12px 14px",
            }}
          >
            {[
              { label: "Repo", value: selectedRun.repo || "-" },
              { label: "Branch", value: selectedRun.run.head_branch || "-" },
              { label: "Triggered by", value: triggeredBy(selectedRun.run) },
              { label: "Runner", value: runnerName(selectedRun.run) },
              { label: "Elapsed", value: selectedRun.elapsed },
              { label: "Status", value: statusLabel(selectedRun.status) },
              ...(timingLabel(selectedRun.run)
                ? [{ label: "Timing", value: timingLabel(selectedRun.run) }]
                : []),
            ].map(({ label, value }) => (
              <div
                key={label}
                style={{
                  alignItems: "center",
                  display: "flex",
                  justifyContent: "space-between",
                }}
              >
                <span style={{ color: "var(--text-muted)", fontSize: 12 }}>
                  {label}
                </span>
                <span
                  style={{
                    color: "var(--text-primary)",
                    fontSize: 13,
                    fontWeight: 500,
                  }}
                >
                  {value}
                </span>
              </div>
            ))}
          </div>

          {/* Action buttons */}
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {selectedRun.run.html_url && (
              <TouchButton
                aria-label="View run on GitHub"
                onClick={() => {
                  window.open(
                    selectedRun.run.html_url,
                    "_blank",
                    "noopener,noreferrer",
                  );
                }}
                variant="default"
                style={{ minHeight: 48, width: "100%" }}
              >
                View on GitHub
              </TouchButton>
            )}

            <TouchButton
              aria-label="Re-run workflow"
              disabled={
                !selectedRun.repo || cancelDone[selectedKey!] === true
              }
              onClick={() => onRerun(selectedRun)}
              variant="primary"
              style={{ minHeight: 48, width: "100%" }}
            >
              Re-run
            </TouchButton>

            {selectedRun.status === "running" && selectedRun.repo && (
              <TouchButton
                aria-label="Cancel run"
                disabled={
                  cancelling[selectedKey!] === true ||
                  cancelDone[selectedKey!] === true
                }
                onClick={() => onCancel(selectedRun)}
                variant="danger"
                style={{ minHeight: 48, width: "100%" }}
              >
                {cancelDone[selectedKey!]
                  ? "Cancelled"
                  : cancelling[selectedKey!]
                    ? "Cancelling..."
                    : "Cancel"}
              </TouchButton>
            )}
          </div>
        </div>
      )}
    </BottomSheet>
  );
}
