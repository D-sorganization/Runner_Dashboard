/**
 * OverviewLeases.tsx — the static "Fair Sharing & Active Leases" (Wave 3)
 * preview panel shown on the Overview tab, extracted (markup 1:1) from the
 * legacy `App.tsx` monolith as part of the decomposition epic (#836, pass 12).
 *
 * Presentational only: this is a hard-coded design-preview card grid (no props,
 * no state, no data fetch) mirroring the legacy inline block exactly. The
 * activity glyph comes from `decompIcons`.
 */
import React from "react";
import { ActivityGlyph } from "./decompIcons";

const h = React.createElement;

export function OverviewLeases(): React.ReactElement {
  return h(
    "div",
    { className: "section", style: { marginTop: "24px" } },
    h(
      "div",
      { className: "section-header", style: { background: "var(--grad-fair)", color: "white" } },
      h("div", { className: "section-title" }, h(ActivityGlyph, { size: 16 }), "Fair Sharing & Active Leases"),
      h(
        "span",
        { className: "section-badge", style: { background: "rgba(255,255,255,0.2)", color: "white" } },
        "Wave 3",
      ),
    ),
    h(
      "div",
      { className: "section-body" },
      h(
        "div",
        { style: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: "16px" } },
        h(
          "div",
          { className: "glass-card", style: { padding: "16px" } },
          h(
            "div",
            { style: { display: "flex", justifyContent: "space-between", marginBottom: "12px" } },
            h("span", { style: { fontWeight: "700", fontSize: "14px" } }, "USER: dieterolson"),
            h("span", { className: "conclusion-badge in_progress" }, "Active"),
          ),
          h(
            "div",
            { className: "metric-row" },
            h("span", { className: "metric-label" }, "Runner:"),
            h("span", { className: "metric-value" }, "ubuntu-latest-4xlarge"),
          ),
          h(
            "div",
            { className: "metric-row" },
            h("span", { className: "metric-label" }, "Lease Time:"),
            h("span", { className: "metric-value" }, "45m / 2h"),
          ),
          h(
            "div",
            { className: "progress-bar", style: { margin: "8px 0" } },
            h("div", { className: "progress-fill blue", style: { width: "37%" } }),
          ),
          h(
            "button",
            {
              className: "btn btn-red",
              style: { width: "100%", marginTop: "8px", justifyContent: "center" },
              "aria-label": "Relinquish runner",
            },
            "Relinquish Runner",
          ),
        ),
        h(
          "div",
          { className: "glass-card", style: { padding: "16px" } },
          h(
            "div",
            { style: { display: "flex", justifyContent: "space-between", marginBottom: "12px" } },
            h("span", { style: { fontWeight: "700", fontSize: "14px" } }, "USER: jules-bot"),
            h("span", { className: "conclusion-badge success" }, "Idle"),
          ),
          h(
            "div",
            { className: "metric-row" },
            h("span", { className: "metric-label" }, "Runner:"),
            h("span", { className: "metric-value" }, "windows-2022-standard"),
          ),
          h(
            "div",
            { className: "metric-row" },
            h("span", { className: "metric-label" }, "Quota Left:"),
            h("span", { className: "metric-value" }, "Unlimited"),
          ),
          h(
            "div",
            { className: "progress-bar", style: { margin: "8px 0" } },
            h("div", { className: "progress-fill purple", style: { width: "100%" } }),
          ),
          h(
            "button",
            {
              className: "btn",
              style: { width: "100%", marginTop: "8px", justifyContent: "center" },
              "aria-label": "View runner logs",
            },
            "View Logs",
          ),
        ),
      ),
    ),
  );
}
