/** Owner-authored feature details; notes are untrusted Markdown, never raw HTML. */
import React from "react";
import { Badge } from "../../primitives/Badge";
import { OwnerMarkdown } from "../../primitives/OwnerMarkdown";
import type { ProjectFeature } from "./types";

export function FeatureDetails({
  features,
}: {
  features: readonly ProjectFeature[];
}): React.ReactElement | null {
  if (features.length === 0) return null;
  return (
    <details style={{ marginTop: 8, fontSize: 13 }}>
      <summary>Features and plans ({features.length})</summary>
      <ul style={{ margin: "4px 0 0 18px", padding: 0 }}>
        {features.map((feature) => (
          <li key={feature.id} style={{ marginTop: 8 }}>
            <code>{feature.id}</code> <strong>{feature.feature}</strong>{" "}
            <Badge tone="neutral" size="sm">
              {feature.status}
            </Badge>
            {feature.tracking !== "-" && (
              <div>Tracking: {feature.tracking}</div>
            )}
            {feature.notes && <OwnerMarkdown text={feature.notes} />}
          </li>
        ))}
      </ul>
    </details>
  );
}
