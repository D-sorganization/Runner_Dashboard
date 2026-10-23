/** Fleet "Security" tab (issue #2571, epic #2566). */
import React from "react";
import { SecurityPanel } from "./SecurityPanel";

export default function SecurityPage(): React.ReactElement {
  return (
    <div className="security-page">
      <div className="section-header">
        <h2 style={{ margin: 0 }}>Security</h2>
      </div>
      <p className="staff-muted">
        Fleet security-scanner contracts, posture and private findings. Findings are untrusted model output and are
        shown as plain text.
      </p>
      <SecurityPanel />
    </div>
  );
}
