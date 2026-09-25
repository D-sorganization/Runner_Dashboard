import React from "react";
import { Badge, type BadgeTone } from "../../primitives/Badge";
import { TouchButton } from "../../primitives/TouchButton";

export interface FleetEventItem {
  id?: number | string;
  event_type?: string;
  kind?: string;
  level?: string;
  severity?: string;
  source?: string;
  node?: string;
  title?: string;
  detail?: string;
  message?: string;
  timestamp?: string;
  ts?: number;
}

export interface FleetEventsSectionProps {
  events?: FleetEventItem[];
  loading?: boolean;
  error?: string | null;
  onRetry?: () => void;
  maxEvents?: number;
}

const LEVEL_TONES: Record<string, BadgeTone> = {
  info: "info",
  warning: "warning",
  error: "danger",
  critical: "danger",
  nominal: "success",
  ok: "success",
};

function getEventLevel(e: FleetEventItem): string {
  return (e.level ?? e.severity ?? "info").toLowerCase();
}

function getEventMessage(e: FleetEventItem): string {
  if (e.message) return e.message;
  if (e.title && e.detail) return `${e.title}: ${e.detail}`;
  return e.title || e.detail || e.event_type || e.kind || "Event";
}

function getEventTimestamp(e: FleetEventItem): string {
  if (e.timestamp) return new Date(e.timestamp).toLocaleTimeString();
  if (e.ts) return new Date(e.ts).toLocaleTimeString();
  return "";
}

export function FleetEventsSection({
  events = [],
  loading = false,
  error = null,
  onRetry,
  maxEvents = 100,
}: FleetEventsSectionProps): React.ReactElement {
  const [selectedLevel, setSelectedLevel] = React.useState<string>("all");

  const filteredEvents = React.useMemo(() => {
    let list = events;
    if (selectedLevel !== "all") {
      list = list.filter((e) => getEventLevel(e) === selectedLevel.toLowerCase());
    }
    return list.slice(0, maxEvents);
  }, [events, selectedLevel, maxEvents]);

  const levelCounts = React.useMemo(() => {
    const counts: Record<string, number> = { all: events.length, info: 0, warning: 0, error: 0 };
    for (const e of events) {
      const lvl = getEventLevel(e);
      if (lvl === "critical" || lvl === "error") counts.error = (counts.error || 0) + 1;
      else if (lvl === "warning") counts.warning = (counts.warning || 0) + 1;
      else counts.info = (counts.info || 0) + 1;
    }
    return counts;
  }, [events]);

  return (
    <section
      id="events"
      className="fleet-section fleet-events-section"
      aria-label="Fleet Event Log"
      style={{
        marginBottom: "1.5rem",
        border: "1px solid var(--border-subtle, rgba(255, 255, 255, 0.1))",
        borderRadius: "8px",
        padding: "1rem",
        backgroundColor: "var(--bg-secondary, #161b22)",
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: "0.5rem",
          marginBottom: "1rem",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
          <h2 style={{ margin: 0, fontSize: "1.2rem", fontWeight: 600 }}>
            Fleet Event Log
          </h2>
          <Badge tone="neutral" size="sm">
            {events.length} events
          </Badge>
        </div>

        <div style={{ display: "flex", gap: "0.4rem", flexWrap: "wrap" }}>
          <TouchButton
            type="button"
            onClick={() => setSelectedLevel("all")}
            aria-label="All events"
            aria-pressed={selectedLevel === "all"}
            style={{
              fontSize: "0.8rem",
              padding: "0.2rem 0.5rem",
              backgroundColor:
                selectedLevel === "all"
                  ? "var(--bg-active, rgba(56, 139, 253, 0.2))"
                  : "transparent",
              borderColor:
                selectedLevel === "all"
                  ? "var(--border-active, #388bfd)"
                  : "var(--border-subtle, rgba(255, 255, 255, 0.1))",
            }}
          >
            All ({levelCounts.all})
          </TouchButton>
          <TouchButton
            type="button"
            onClick={() => setSelectedLevel("info")}
            aria-label="Info events"
            aria-pressed={selectedLevel === "info"}
            style={{
              fontSize: "0.8rem",
              padding: "0.2rem 0.5rem",
              backgroundColor:
                selectedLevel === "info"
                  ? "var(--bg-active, rgba(56, 139, 253, 0.2))"
                  : "transparent",
              borderColor:
                selectedLevel === "info"
                  ? "var(--border-active, #388bfd)"
                  : "var(--border-subtle, rgba(255, 255, 255, 0.1))",
            }}
          >
            Info ({levelCounts.info})
          </TouchButton>
          <TouchButton
            type="button"
            onClick={() => setSelectedLevel("warning")}
            aria-label="Warning events"
            aria-pressed={selectedLevel === "warning"}
            style={{
              fontSize: "0.8rem",
              padding: "0.2rem 0.5rem",
              backgroundColor:
                selectedLevel === "warning"
                  ? "var(--bg-warning-subtle, rgba(210, 153, 34, 0.2))"
                  : "transparent",
              borderColor:
                selectedLevel === "warning"
                  ? "var(--border-warning, #d29922)"
                  : "var(--border-subtle, rgba(255, 255, 255, 0.1))",
            }}
          >
            Warning ({levelCounts.warning})
          </TouchButton>
          <TouchButton
            type="button"
            onClick={() => setSelectedLevel("error")}
            aria-label="Error events"
            aria-pressed={selectedLevel === "error"}
            style={{
              fontSize: "0.8rem",
              padding: "0.2rem 0.5rem",
              backgroundColor:
                selectedLevel === "error"
                  ? "var(--bg-danger-subtle, rgba(248, 81, 73, 0.2))"
                  : "transparent",
              borderColor:
                selectedLevel === "error"
                  ? "var(--border-danger, #f85149)"
                  : "var(--border-subtle, rgba(255, 255, 255, 0.1))",
            }}
          >
            Error ({levelCounts.error})
          </TouchButton>
        </div>
      </div>

      {error ? (
        <div
          role="alert"
          style={{
            padding: "0.75rem",
            marginBottom: "1rem",
            borderRadius: "6px",
            backgroundColor: "var(--bg-danger-subtle, rgba(248, 81, 73, 0.15))",
            border: "1px solid var(--border-danger, #f85149)",
            color: "var(--text-danger, #f85149)",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: "0.5rem",
          }}
        >
          <span>{error}</span>
          {onRetry ? (
            <TouchButton
              type="button"
              onClick={onRetry}
              aria-label="Retry"
              style={{
                fontSize: "0.8rem",
                padding: "0.2rem 0.5rem",
                cursor: "pointer",
              }}
            >
              Retry
            </TouchButton>
          ) : null}
        </div>
      ) : null}

      {loading && events.length === 0 ? (
        <div
          style={{
            padding: "1.5rem",
            textAlign: "center",
            color: "var(--text-muted, #8b949e)",
            fontSize: "0.9rem",
          }}
        >
          Loading fleet events…
        </div>
      ) : !error && filteredEvents.length === 0 ? (
        <div
          style={{
            padding: "1.5rem",
            textAlign: "center",
            color: "var(--text-muted, #8b949e)",
            fontSize: "0.9rem",
          }}
        >
          {events.length === 0
            ? "No fleet events recorded yet."
            : `No events match the "${selectedLevel}" filter.`}
        </div>
      ) : null}

      {!error && filteredEvents.length > 0 ? (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: "0.4rem",
            maxHeight: "360px",
            overflowY: "auto",
          }}
        >
          {filteredEvents.map((event, idx) => {
            const level = getEventLevel(event);
            const tone = LEVEL_TONES[level] ?? "neutral";
            const source = event.source || event.node;
            const message = getEventMessage(event);
            const time = getEventTimestamp(event);
            const key = event.id ?? `${event.ts ?? idx}-${message}`;
            return (
              <div
                key={key}
                style={{
                  padding: "0.5rem 0.75rem",
                  borderRadius: "6px",
                  backgroundColor: "var(--bg-tertiary, #21262d)",
                  border: "1px solid var(--border-subtle, rgba(255, 255, 255, 0.05))",
                  display: "flex",
                  alignItems: "flex-start",
                  gap: "0.75rem",
                  fontSize: "0.85rem",
                }}
              >
                <div style={{ minWidth: "60px", paddingTop: "0.1rem" }}>
                  <Badge tone={tone} size="sm">
                    {event.level ?? event.severity ?? "info"}
                  </Badge>
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: "flex", alignItems: "baseline", gap: "0.5rem", flexWrap: "wrap" }}>
                    {source ? (
                      <span
                        style={{
                          fontWeight: 600,
                          fontSize: "0.8rem",
                          color: "var(--text-accent, #58a6ff)",
                        }}
                      >
                        [{source}]
                      </span>
                    ) : null}
                    <span style={{ color: "var(--text-primary, #c9d1d9)", wordBreak: "break-word" }}>
                      {message}
                    </span>
                  </div>
                </div>
                <div
                  style={{
                    fontSize: "0.75rem",
                    color: "var(--text-muted, #8b949e)",
                    whiteSpace: "nowrap",
                    paddingTop: "0.1rem",
                  }}
                >
                  {time}
                </div>
              </div>
            );
          })}
        </div>
      ) : null}
    </section>
  );
}

export default FleetEventsSection;
