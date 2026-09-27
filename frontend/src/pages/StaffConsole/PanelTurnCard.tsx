/**
 * PanelTurnCard.tsx — One turn in an expert panel deliberation (SC-D/Issue #1635).
 *
 * Shows stance chip (agree / partly / disagree), error/timeout status when not ok,
 * one-line position, and expert markdown body.
 */
import React from "react";
import { Badge } from "../../primitives/Badge";
import type { PanelTurnMeta } from "./panelTurn";
import { ThreadMarkdown } from "./threadMarkdown";
import type { ThreadMessage } from "./threadTypes";
import "./panel.css";

export interface PanelTurnCardProps {
  message: ThreadMessage;
  meta: PanelTurnMeta;
}

export function PanelTurnCard({ message, meta }: PanelTurnCardProps) {
  const isOk = meta.status === "ok";

  const stanceTone = (stance: string | null): "success" | "warning" | "danger" | "neutral" => {
    switch (stance?.toLowerCase()) {
      case "agree":
        return "success";
      case "partly":
        return "warning";
      case "disagree":
        return "danger";
      default:
        return "neutral";
    }
  };

  return (
    <div
      className="panel-turn-card"
      data-testid={`panel-turn-${message.id}`}
      style={{
        background: "var(--bg-tertiary, #161b22)",
        border: "1px solid var(--border, #30363d)",
        borderRadius: "8px",
        padding: "12px 14px",
        maxWidth: "85%",
        wordBreak: "break-word",
        fontSize: 13,
        lineHeight: 1.5,
      }}
    >
      <div
        className="panel-turn-card__meta"
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          marginBottom: 6,
          flexWrap: "wrap",
        }}
      >
        {meta.stance && (
          <Badge
            size="sm"
            tone={stanceTone(meta.stance)}
            data-testid={`stance-chip-${meta.stance.toLowerCase()}`}
          >
            {meta.stance.charAt(0).toUpperCase() + meta.stance.slice(1)}
          </Badge>
        )}
        {!isOk && (
          <Badge
            size="sm"
            tone="danger"
            data-testid={`status-chip-${meta.status.toLowerCase()}`}
          >
            {meta.status.toUpperCase()}
          </Badge>
        )}
      </div>

      {meta.position && (
        <div
          className="panel-turn-card__position"
          data-testid="panel-position"
          style={{
            fontStyle: "italic",
            marginBottom: 8,
            color: "var(--text-secondary, #c9d1d9)",
            borderLeft: "3px solid var(--border, #30363d)",
            paddingLeft: 8,
          }}
        >
          {meta.position}
        </div>
      )}

      <div className="panel-turn-card__body">
        <ThreadMarkdown content={message.body_md} />
      </div>
    </div>
  );
}
