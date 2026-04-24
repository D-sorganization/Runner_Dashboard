import { useState, useEffect, useCallback, useRef } from "react";
import { Activity, Power, PowerOff, RefreshCw, CheckCircle, XCircle, Clock, Cpu, GitBranch, ChevronDown, ChevronUp, Settings, BarChart3, History, Zap, AlertTriangle, Server, Plus, Minus, Eye, ArrowUpDown } from "lucide-react";

// ─── Configuration ───────────────────────────────────────────────────────────
const API_BASE = "http://localhost:8321/api";
const POLL_INTERVAL = 10000;

// ─── Theme ───────────────────────────────────────────────────────────────────
const colors = {
  bg: "#0f1117",
  surface: "#161b22",
  surfaceHover: "#1c2129",
  border: "#30363d",
  borderLight: "#3d444d",
  text: "#e6edf3",
  textMuted: "#8b949e",
  textDim: "#6e7681",
  accent: "#58a6ff",
  accentDim: "#1f6feb",
  green: "#3fb950",
  greenDim: "#238636",
  greenBg: "rgba(63, 185, 80, 0.1)",
  red: "#f85149",
  redDim: "#da3633",
  redBg: "rgba(248, 81, 73, 0.1)",
  yellow: "#d29922",
  yellowBg: "rgba(210, 153, 34, 0.1)",
  purple: "#bc8cff",
  purpleBg: "rgba(188, 140, 255, 0.1)",
  orange: "#f0883e",
};

// ─── Helpers ─────────────────────────────────────────────────────────────────
function timeAgo(dateStr) {
  if (!dateStr) return "—";
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

function duration(startStr, endStr) {
  if (!startStr || !endStr) return "—";
  const diff = new Date(endStr) - new Date(startStr);
  const secs = Math.floor(diff / 1000);
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60);
  const remSecs = secs % 60;
  if (mins < 60) return `${mins}m ${remSecs}s`;
  const hrs = Math.floor(mins / 60);
  return `${hrs}h ${mins % 60}m`;
}

function statusColor(status) {
  if (status === "completed" || status === "success" || status === "online") return colors.green;
  if (status === "failure" || status === "offline" || status === "cancelled") return colors.red;
  if (status === "in_progress" || status === "queued" || status === "busy") return colors.yellow;
  return colors.textMuted;
}

function statusBg(status) {
  if (status === "completed" || status === "success" || status === "online") return colors.greenBg;
  if (status === "failure" || status === "offline" || status === "cancelled") return colors.redBg;
  if (status === "in_progress" || status === "queued" || status === "busy") return colors.yellowBg;
  return "rgba(139, 148, 158, 0.1)";
}

// ─── Badge Component ─────────────────────────────────────────────────────────
function Badge({ status, label }) {
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 6,
      padding: "3px 10px", borderRadius: 20, fontSize: 12, fontWeight: 600,
      color: statusColor(status), background: statusBg(status),
      border: `1px solid ${statusColor(status)}33`,
      textTransform: "capitalize",
    }}>
      <span style={{
        width: 7, height: 7, borderRadius: "50%",
        background: statusColor(status),
        boxShadow: `0 0 6px ${statusColor(status)}88`,
      }} />
      {label || status}
    </span>
  );
}

// ─── Stat Card ───────────────────────────────────────────────────────────────
function StatCard({ icon: Icon, label, value, color, subtitle }) {
  return (
    <div style={{
      background: colors.surface, border: `1px solid ${colors.border}`,
      borderRadius: 12, padding: "20px 24px", flex: 1, minWidth: 180,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
        <div style={{
          width: 36, height: 36, borderRadius: 10,
          background: `${color}18`, display: "flex",
          alignItems: "center", justifyContent: "center",
        }}>
          <Icon size={18} color={color} />
        </div>
        <span style={{ color: colors.textMuted, fontSize: 13, fontWeight: 500 }}>{label}</span>
      </div>
      <div style={{ fontSize: 32, fontWeight: 700, color: colors.text, lineHeight: 1.1 }}>{value}</div>
      {subtitle && <div style={{ color: colors.textDim, fontSize: 12, marginTop: 4 }}>{subtitle}</div>}
    </div>
  );
}

// ─── Runner Card ─────────────────────────────────────────────────────────────
function RunnerCard({ runner, onToggle, toggling }) {
  const isOnline = runner.status === "online";
  const isBusy = runner.busy;

  return (
    <div style={{
      background: colors.surface, border: `1px solid ${colors.border}`,
      borderRadius: 12, padding: 20, transition: "border-color 0.2s",
      ...(isBusy ? { borderColor: colors.yellow + "66" } : {}),
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 14 }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
            <Server size={16} color={isOnline ? colors.green : colors.red} />
            <span style={{ fontWeight: 600, fontSize: 15, color: colors.text }}>{runner.name}</span>
          </div>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 6 }}>
            {runner.labels?.filter(l => !["self-hosted", "Linux", "X64"].includes(l.name)).map(l => (
              <span key={l.id} style={{
                fontSize: 11, padding: "2px 8px", borderRadius: 12,
                background: colors.purpleBg, color: colors.purple,
                border: `1px solid ${colors.purple}33`,
              }}>{l.name}</span>
            ))}
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <Badge status={isBusy ? "busy" : runner.status} label={isBusy ? "busy" : runner.status} />
          <button
            onClick={() => onToggle(runner)}
            disabled={toggling}
            title={isOnline ? "Stop runner" : "Start runner"}
            style={{
              width: 36, height: 36, borderRadius: 10, border: "1px solid " + colors.border,
              background: isOnline ? colors.redBg : colors.greenBg,
              color: isOnline ? colors.red : colors.green,
              cursor: toggling ? "wait" : "pointer",
              display: "flex", alignItems: "center", justifyContent: "center",
              opacity: toggling ? 0.5 : 1, transition: "all 0.2s",
            }}
          >
            {isOnline ? <PowerOff size={16} /> : <Power size={16} />}
          </button>
        </div>
      </div>
      <div style={{
        display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8,
        padding: "12px 0 0", borderTop: `1px solid ${colors.border}`,
      }}>
        <div>
          <div style={{ fontSize: 11, color: colors.textDim, marginBottom: 2 }}>OS</div>
          <div style={{ fontSize: 13, color: colors.textMuted }}>{runner.os || "Linux"}</div>
        </div>
        <div>
          <div style={{ fontSize: 11, color: colors.textDim, marginBottom: 2 }}>ID</div>
          <div style={{ fontSize: 13, color: colors.textMuted, fontFamily: "monospace" }}>#{runner.id}</div>
        </div>
      </div>
    </div>
  );
}

// ─── Workflow Run Row ────────────────────────────────────────────────────────
function WorkflowRunRow({ run }) {
  const [expanded, setExpanded] = useState(false);
  const conclusion = run.conclusion || run.status;

  return (
    <div style={{
      borderBottom: `1px solid ${colors.border}`,
      transition: "background 0.15s",
    }}>
      <div
        onClick={() => setExpanded(!expanded)}
        style={{
          display: "grid", gridTemplateColumns: "32px 2fr 1.2fr 100px 100px 90px 32px",
          alignItems: "center", gap: 12, padding: "12px 16px", cursor: "pointer",
        }}
        onMouseEnter={e => e.currentTarget.style.background = colors.surfaceHover}
        onMouseLeave={e => e.currentTarget.style.background = "transparent"}
      >
        <div style={{ display: "flex", justifyContent: "center" }}>
          {conclusion === "success" ? <CheckCircle size={16} color={colors.green} /> :
           conclusion === "failure" ? <XCircle size={16} color={colors.red} /> :
           conclusion === "in_progress" ? <RefreshCw size={16} color={colors.yellow} className="spin" /> :
           conclusion === "cancelled" ? <XCircle size={16} color={colors.textDim} /> :
           <Clock size={16} color={colors.yellow} />}
        </div>
        <div>
          <div style={{ fontSize: 14, fontWeight: 500, color: colors.text }}>{run.name}</div>
          <div style={{ fontSize: 12, color: colors.textMuted }}>{run.head_branch}</div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <GitBranch size={13} color={colors.textDim} />
          <span style={{ fontSize: 13, color: colors.textMuted, fontFamily: "monospace" }}>
            {run.repository?.name || "—"}
          </span>
        </div>
        <Badge status={conclusion} />
        <span style={{ fontSize: 12, color: colors.textMuted }}>
          {duration(run.run_started_at || run.created_at, run.updated_at)}
        </span>
        <span style={{ fontSize: 12, color: colors.textDim }}>{timeAgo(run.updated_at)}</span>
        <div style={{ display: "flex", justifyContent: "center", color: colors.textDim }}>
          {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </div>
      </div>
      {expanded && (
        <div style={{
          padding: "8px 16px 16px 60px", background: colors.surfaceHover,
          fontSize: 13, color: colors.textMuted, display: "grid",
          gridTemplateColumns: "1fr 1fr 1fr", gap: 12,
        }}>
          <div>
            <span style={{ color: colors.textDim }}>Event:</span> {run.event}
          </div>
          <div>
            <span style={{ color: colors.textDim }}>Run #:</span> {run.run_number}
          </div>
          <div>
            <span style={{ color: colors.textDim }}>Attempt:</span> {run.run_attempt}
          </div>
          <div>
            <span style={{ color: colors.textDim }}>Started:</span> {new Date(run.run_started_at || run.created_at).toLocaleString()}
          </div>
          <div>
            <span style={{ color: colors.textDim }}>Commit:</span>{" "}
            <span style={{ fontFamily: "monospace" }}>{run.head_sha?.slice(0, 7)}</span>
          </div>
          <div>
            <a href={run.html_url} target="_blank" rel="noopener noreferrer"
              style={{ color: colors.accent, textDecoration: "none" }}>
              View on GitHub
            </a>
          </div>
        </div>
      )}
    </div>
  );
}

function QueuePanel({ queue, onRefresh }) {
  const queued = queue?.queued || [];
  const inProgress = queue?.in_progress || [];
  const rows = [...queued, ...inProgress];

  return (
    <>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 20, gap: 12, flexWrap: "wrap" }}>
        <div>
          <h2 style={{ fontSize: 18, fontWeight: 600, margin: 0 }}>Queue</h2>
          <div style={{ fontSize: 13, color: colors.textMuted, marginTop: 4 }}>
            Queued and in-progress workflows across the recent org sample.
          </div>
        </div>
        <button
          onClick={onRefresh}
          style={{
            display: "flex", alignItems: "center", gap: 6,
            padding: "8px 14px", borderRadius: 10, border: `1px solid ${colors.border}`,
            background: colors.surface, color: colors.textMuted, cursor: "pointer", fontSize: 13,
          }}
        >
          <RefreshCw size={14} /> Refresh queue
        </button>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(180px, 1fr))", gap: 16, marginBottom: 16 }}>
        <StatCard icon={Clock} label="Queued" value={queue?.queued_count ?? 0} color={colors.yellow} subtitle="waiting for runners" />
        <StatCard icon={RefreshCw} label="Running" value={queue?.in_progress_count ?? 0} color={colors.accent} subtitle="active now" />
        <StatCard icon={Activity} label="Total" value={queue?.total ?? 0} color={colors.green} subtitle="current queue sample" />
      </div>
      <div style={{
        background: colors.surface, border: `1px solid ${colors.border}`,
        borderRadius: 12, overflow: "hidden",
      }}>
        <div style={{
          display: "grid", gridTemplateColumns: "1.4fr 1fr 110px 110px 100px",
          gap: 12, padding: "10px 16px", borderBottom: `1px solid ${colors.border}`,
          fontSize: 11, color: colors.textDim, fontWeight: 600, textTransform: "uppercase",
          letterSpacing: "0.5px",
        }}>
          <div>Workflow</div>
          <div>Repository</div>
          <div>Status</div>
          <div>Run #</div>
          <div>Created</div>
        </div>
        {rows.map(run => (
          <div
            key={`${run.id}-${run.status}`}
            style={{
              display: "grid", gridTemplateColumns: "1.4fr 1fr 110px 110px 100px",
              gap: 12, padding: "12px 16px", borderBottom: `1px solid ${colors.border}`,
            }}
          >
            <div>
              <div style={{ fontSize: 14, color: colors.text }}>{run.name || "Workflow"}</div>
              <div style={{ fontSize: 12, color: colors.textMuted }}>{run.head_branch || "—"}</div>
            </div>
            <div style={{ fontSize: 13, color: colors.textMuted }}>{run.repository?.name || "—"}</div>
            <div><Badge status={run.status || "queued"} label={run.status || "queued"} /></div>
            <div style={{ fontSize: 13, color: colors.textMuted }}>{run.run_number ?? "—"}</div>
            <div style={{ fontSize: 12, color: colors.textDim }}>{timeAgo(run.created_at)}</div>
          </div>
        ))}
        {rows.length === 0 && (
          <div style={{ padding: 40, textAlign: "center", color: colors.textDim }}>
            Queue is empty.
          </div>
        )}
      </div>
    </>
  );
}

// ─── MATLAB Runner Health (issue #570) ───────────────────────────────────────
// Windows MATLAB lint capacity is a single point of failure across the fleet:
// when the ControlTower Windows runner is offline, MATLAB Code Analyzer jobs
// queue forever with no visible signal.  This panel surfaces status + an
// actionable warning so operators can diagnose before CI jobs stall.
function MatlabRunnerPanel({ health }) {
  if (!health) return null;
  const { runners = [], total = 0, online = 0, busy = 0, warning, recent_workflow_runs = [] } = health;
  const hasRunners = total > 0;
  const allOnline = hasRunners && online === total;
  const borderColor = warning ? (hasRunners ? colors.yellow : colors.red) : colors.greenDim;
  const headerColor = warning ? (hasRunners ? colors.yellow : colors.red) : colors.green;

  return (
    <div style={{
      background: colors.surface, border: `1px solid ${borderColor}`,
      borderRadius: 12, padding: 16, marginBottom: 16,
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{
            display: "inline-block", padding: "2px 8px", borderRadius: 6,
            background: "#e16737", color: "#fff", fontSize: 11, fontWeight: 700,
          }}>MATLAB</span>
          <h3 style={{ margin: 0, fontSize: 15, fontWeight: 600, color: headerColor }}>
            Windows MATLAB Runner Health
          </h3>
        </div>
        <span style={{ fontSize: 12, color: colors.textMuted }}>
          {hasRunners ? `${online}/${total} online, ${busy} busy` : "no runners registered"}
        </span>
      </div>

      {warning && (
        <div style={{
          padding: "8px 12px", borderRadius: 8, marginBottom: 10,
          background: (hasRunners ? colors.yellow : colors.red) + "22",
          color: hasRunners ? colors.yellow : colors.red,
          fontSize: 12, fontWeight: 500,
        }}>
          {warning}
        </div>
      )}

      {hasRunners && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: 8, marginBottom: recent_workflow_runs.length ? 10 : 0 }}>
          {runners.map(r => {
            const isOnline = r.status === "online";
            const dotColor = !isOnline ? colors.red : r.busy ? colors.yellow : colors.green;
            return (
              <div key={r.id || r.name} style={{
                padding: "8px 10px", borderRadius: 8,
                background: colors.bg, border: `1px solid ${colors.border}`,
                fontSize: 12,
              }}>
                <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
                  <span style={{ width: 8, height: 8, borderRadius: "50%", background: dotColor, display: "inline-block" }} />
                  <span style={{ fontWeight: 600, color: colors.text, wordBreak: "break-all" }}>{r.name}</span>
                </div>
                <div style={{ color: colors.textMuted, fontSize: 11 }}>
                  {r.os || "Windows"} &middot; {r.busy ? "busy" : isOnline ? "idle" : "offline"}
                  {r.persistence ? ` · ${r.persistence.replace("_", " ")}` : ""}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {recent_workflow_runs.length > 0 && (
        <div style={{ borderTop: `1px solid ${colors.border}`, paddingTop: 8 }}>
          <div style={{ fontSize: 11, fontWeight: 600, color: colors.textMuted, marginBottom: 6 }}>
            Recent MATLAB Code Analyzer runs
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {recent_workflow_runs.slice(0, 5).map(run => {
              const concColor = run.conclusion === "success" ? colors.green
                : run.conclusion === "failure" ? colors.red
                : colors.textMuted;
              return (
                <a
                  key={run.run_id || run.html_url}
                  href={run.html_url || "#"}
                  target="_blank"
                  rel="noreferrer"
                  style={{ color: colors.text, textDecoration: "none", fontSize: 12, display: "flex", justifyContent: "space-between", gap: 8 }}
                >
                  <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {run.repo} &middot; {run.name}
                  </span>
                  <span style={{ color: concColor, fontWeight: 600, whiteSpace: "nowrap" }}>
                    {run.conclusion || run.status}
                  </span>
                </a>
              );
            })}
          </div>
        </div>
      )}

      {allOnline && !warning && recent_workflow_runs.length === 0 && (
        <div style={{ fontSize: 11, color: colors.textMuted }}>
          MATLAB lint capacity available. No recent Code Analyzer runs found.
        </div>
      )}
    </div>
  );
}

// ─── Fleet Scaler ────────────────────────────────────────────────────────────
function FleetScaler({ runners, onScale, scaling }) {
  const online = runners.filter(r => r.status === "online").length;
  const total = runners.length;

  return (
    <div style={{
      background: colors.surface, border: `1px solid ${colors.border}`,
      borderRadius: 12, padding: 20,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 16 }}>
        <Settings size={18} color={colors.accent} />
        <span style={{ fontWeight: 600, fontSize: 15, color: colors.text }}>Fleet Control</span>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 16, marginBottom: 16 }}>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 12, color: colors.textDim, marginBottom: 4 }}>Active Runners</div>
          <div style={{ fontSize: 36, fontWeight: 700, color: colors.text }}>{online} <span style={{ fontSize: 16, fontWeight: 400, color: colors.textMuted }}>/ {total}</span></div>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button
            onClick={() => onScale("down")}
            disabled={scaling || online === 0}
            style={{
              width: 44, height: 44, borderRadius: 12, border: `1px solid ${colors.border}`,
              background: colors.redBg, color: colors.red, cursor: "pointer",
              display: "flex", alignItems: "center", justifyContent: "center",
              opacity: (scaling || online === 0) ? 0.4 : 1, transition: "all 0.2s",
            }}
          >
            <Minus size={20} />
          </button>
          <button
            onClick={() => onScale("up")}
            disabled={scaling || online === total}
            style={{
              width: 44, height: 44, borderRadius: 12, border: `1px solid ${colors.border}`,
              background: colors.greenBg, color: colors.green, cursor: "pointer",
              display: "flex", alignItems: "center", justifyContent: "center",
              opacity: (scaling || online === total) ? 0.4 : 1, transition: "all 0.2s",
            }}
          >
            <Plus size={20} />
          </button>
        </div>
      </div>
      <div style={{ display: "flex", gap: 4, marginBottom: 8 }}>
        {runners.map((r, i) => (
          <div key={i} style={{
            flex: 1, height: 8, borderRadius: 4,
            background: r.status === "online"
              ? (r.busy ? colors.yellow : colors.green)
              : colors.border,
            transition: "background 0.3s",
          }} title={`${r.name}: ${r.busy ? "busy" : r.status}`} />
        ))}
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: colors.textDim }}>
        <span>idle</span>
        <span>
          {runners.filter(r => r.busy).length} busy &middot; {online - runners.filter(r => r.busy).length} idle &middot; {total - online} offline
        </span>
      </div>
      <div style={{ marginTop: 16, display: "flex", gap: 8 }}>
        <button
          onClick={() => onScale("all-up")}
          disabled={scaling || online === total}
          style={{
            flex: 1, padding: "10px 0", borderRadius: 10, border: `1px solid ${colors.greenDim}`,
            background: colors.greenDim + "33", color: colors.green, fontWeight: 600,
            fontSize: 13, cursor: "pointer", transition: "all 0.2s",
            opacity: (scaling || online === total) ? 0.4 : 1,
          }}
        >
          Start All
        </button>
        <button
          onClick={() => onScale("all-down")}
          disabled={scaling || online === 0}
          style={{
            flex: 1, padding: "10px 0", borderRadius: 10, border: `1px solid ${colors.redDim}`,
            background: colors.redDim + "33", color: colors.red, fontWeight: 600,
            fontSize: 13, cursor: "pointer", transition: "all 0.2s",
            opacity: (scaling || online === 0) ? 0.4 : 1,
          }}
        >
          Stop All
        </button>
      </div>
    </div>
  );
}

// ─── Tab Button ──────────────────────────────────────────────────────────────
function TabButton({ active, icon: Icon, label, onClick, count }) {
  return (
    <button onClick={onClick} style={{
      display: "flex", alignItems: "center", gap: 8,
      padding: "10px 18px", borderRadius: 10, border: "none",
      background: active ? colors.accentDim + "44" : "transparent",
      color: active ? colors.accent : colors.textMuted,
      fontWeight: active ? 600 : 400, fontSize: 14, cursor: "pointer",
      transition: "all 0.2s",
    }}>
      <Icon size={16} />
      {label}
      {count !== undefined && (
        <span style={{
          fontSize: 11, padding: "1px 7px", borderRadius: 10,
          background: active ? colors.accentDim + "66" : colors.border,
          color: active ? colors.accent : colors.textDim,
        }}>{count}</span>
      )}
    </button>
  );
}

// ─── Machines Master-Detail ──────────────────────────────────────────────────
function MachineCard({ name, data, hardware, selected, onClick }) {
  const isOffline = data.status === "offline";
  const specs = hardware?.hardware_specs || data.hardware_specs || {};
  const cpu = isOffline ? 0 : (data.cpu?.percent_1m_avg || data.cpu?.percent || 0);
  const ram = isOffline ? 0 : (data.memory?.percent || 0);
  const logical = specs.cpu_logical_cores || data.cpu?.cores_logical || "?";
  const memoryGb = specs.memory_gb || data.memory?.total_gb || "?";
  return (
    <button onClick={onClick} style={{
      textAlign: "left", width: "100%", cursor: "pointer",
      background: selected ? colors.accentDim + "22" : colors.surface,
      border: `1px solid ${selected ? colors.accent : colors.border}`,
      borderRadius: 10, padding: 14, marginBottom: 10,
      color: colors.text,
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, fontWeight: 600, fontSize: 14 }}>
          <span style={{
            width: 8, height: 8, borderRadius: "50%",
            background: isOffline ? colors.red : colors.green,
          }} />
          {name} {data._role === "hub" && <span title="hub" style={{ color: colors.yellow }}>★</span>}
        </div>
        <span style={{ fontSize: 11, color: colors.textDim }}>
          {isOffline ? "offline" : `${logical}c / ${memoryGb}GB`}
        </span>
      </div>
      <div style={{ display: "flex", gap: 6, height: 4 }}>
        <div style={{ flex: 1, background: colors.border, borderRadius: 2, overflow: "hidden" }}>
          <div style={{ height: "100%", width: `${cpu}%`, background: cpu > 80 ? colors.red : colors.accent }} />
        </div>
        <div style={{ flex: 1, background: colors.border, borderRadius: 2, overflow: "hidden" }}>
          <div style={{ height: "100%", width: `${ram}%`, background: ram > 80 ? colors.orange : colors.purple }} />
        </div>
      </div>
    </button>
  );
}

function MachineDetailPane({ name, data, hardware, runners, runs }) {
  if (!name || !data) {
    return (
      <div style={{ padding: 48, textAlign: "center", color: colors.textDim }}>
        <Cpu size={36} style={{ opacity: 0.4, marginBottom: 12 }} />
        <div style={{ fontSize: 14 }}>Select a machine to see details.</div>
      </div>
    );
  }
  const isOffline = data.status === "offline";
  const specs = hardware?.hardware_specs || data.hardware_specs || {};
  const capacity = hardware?.workload_capacity || data.workload_capacity || {};
  const cpuPct = isOffline ? 0 : (data.cpu?.percent_1m_avg || data.cpu?.percent || 0);
  const ramPct = isOffline ? 0 : (data.memory?.percent || 0);
  const diskPct = isOffline ? 0 : (data.disk?.percent || 0);
  const lastSeen = data.last_seen || data.updated_at;
  const reason = data.offline_reason;
  const machineRunners = runners.filter(r => (r.machine || r.hostname || r.labels?.join(",") || "").includes(name));
  const machineRuns = runs.filter(run => {
    const target = [run.runner_name, run.machine, run.hostname, run.machine_name].filter(Boolean).join(",");
    return target.includes(name);
  }).slice(0, 10);
  const logs = data.logs_tail || data.recent_logs || [];
  const Stat = ({ label, value, pct }) => (
    <div style={{
      background: colors.bg, border: `1px solid ${colors.border}`,
      borderRadius: 8, padding: 12,
    }}>
      <div style={{ fontSize: 11, color: colors.textDim, textTransform: "uppercase", letterSpacing: 0.5 }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 600, marginTop: 4 }}>{value}</div>
      {pct !== undefined && (
        <div style={{ height: 4, background: colors.border, borderRadius: 2, marginTop: 8, overflow: "hidden" }}>
          <div style={{ height: "100%", width: `${pct}%`, background: pct > 80 ? colors.red : colors.accent }} />
        </div>
      )}
    </div>
  );
  return (
    <div style={{ padding: 20 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}>
        <div>
          <div style={{ fontSize: 20, fontWeight: 700 }}>{name}</div>
          <div style={{ fontSize: 12, color: colors.textDim, marginTop: 4 }}>
            {isOffline
              ? `Offline · reason: ${reason || "unknown"} · last seen ${timeAgo(lastSeen)}`
              : `Online · last seen ${timeAgo(lastSeen)}`}
          </div>
        </div>
        <span style={{
          padding: "4px 10px", borderRadius: 8, fontSize: 11, fontWeight: 600,
          background: isOffline ? colors.redBg : colors.greenBg,
          color: isOffline ? colors.red : colors.green,
        }}>
          {isOffline ? "OFFLINE" : "ONLINE"}
        </span>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))", gap: 10, marginBottom: 20 }}>
        <Stat label="CPU" value={`${Math.round(cpuPct)}%`} pct={cpuPct} />
        <Stat label="Memory" value={`${Math.round(ramPct)}%`} pct={ramPct} />
        <Stat label="Disk" value={`${Math.round(diskPct)}%`} pct={diskPct} />
        <Stat label="Cores" value={specs.cpu_logical_cores || data.cpu?.cores_logical || "?"} />
        <Stat label="Memory GB" value={specs.memory_gb || data.memory?.total_gb || "?"} />
        <Stat label="Runners" value={capacity.max_runners ?? machineRunners.length} />
      </div>

      {capacity.tags?.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <div style={{ fontSize: 12, color: colors.textDim, marginBottom: 6 }}>Tags / queue affinity</div>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {capacity.tags.map(t => (
              <span key={t} style={{
                padding: "3px 10px", borderRadius: 10, fontSize: 11,
                background: colors.accentDim + "33", color: colors.accent,
              }}>{t}</span>
            ))}
          </div>
        </div>
      )}

      <div style={{ marginBottom: 20 }}>
        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>Runners on this machine</div>
        {machineRunners.length === 0 ? (
          <div style={{ fontSize: 12, color: colors.textDim }}>No runner metadata matched.</div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {machineRunners.map(r => (
              <div key={r.id || r.name} style={{
                display: "flex", justifyContent: "space-between",
                background: colors.bg, padding: "6px 10px",
                border: `1px solid ${colors.border}`, borderRadius: 6, fontSize: 12,
              }}>
                <span>{r.name}</span>
                <span style={{ color: statusColor(r.status) }}>{r.status} {r.busy ? "(busy)" : ""}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      <div style={{ marginBottom: 20 }}>
        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>Recent jobs</div>
        {machineRuns.length === 0 ? (
          <div style={{ fontSize: 12, color: colors.textDim }}>No recent jobs associated.</div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {machineRuns.map(run => (
              <div key={run.id} style={{
                display: "flex", justifyContent: "space-between",
                padding: "6px 10px", background: colors.bg,
                border: `1px solid ${colors.border}`, borderRadius: 6, fontSize: 12,
              }}>
                <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {run.name || run.workflow_name || `run ${run.id}`}
                </span>
                <span style={{ color: statusColor(run.conclusion || run.status), marginLeft: 8 }}>
                  {run.conclusion || run.status} · {timeAgo(run.updated_at)}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      <div>
        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>Logs tail</div>
        <pre style={{
          background: colors.bg, border: `1px solid ${colors.border}`,
          borderRadius: 6, padding: 10, fontSize: 11, color: colors.textMuted,
          maxHeight: 200, overflow: "auto", margin: 0,
        }}>
{logs.length === 0 ? "(no log tail available)" : (Array.isArray(logs) ? logs.join("\n") : String(logs))}
        </pre>
      </div>
    </div>
  );
}

function MachinesMasterDetail({
  fleetStatus, fleetHardware, runners, runs,
  selectedMachine, setSelectedMachine,
  machineSearch, setMachineSearch,
  machineStatusFilter, setMachineStatusFilter,
}) {
  const isNarrow = typeof window !== "undefined" && window.innerWidth < 900;
  const entries = Object.entries(fleetStatus).filter(([name, data]) => {
    if (machineSearch && !name.toLowerCase().includes(machineSearch.toLowerCase())) return false;
    if (machineStatusFilter === "online" && data.status === "offline") return false;
    if (machineStatusFilter === "offline" && data.status !== "offline") return false;
    return true;
  });
  const selectedData = selectedMachine ? fleetStatus[selectedMachine] : null;
  const selectedHardware = selectedMachine
    ? fleetHardware.find(h => h.name === selectedMachine)
    : null;

  const listPane = (
    <div style={{
      background: colors.surface, border: `1px solid ${colors.border}`,
      borderRadius: 12, padding: 12, overflow: "auto", maxHeight: "calc(100vh - 200px)",
    }}>
      <div style={{ display: "flex", gap: 6, marginBottom: 10 }}>
        <input
          type="text" value={machineSearch}
          onChange={e => setMachineSearch(e.target.value)}
          placeholder="Search machines…"
          style={{
            flex: 1, padding: "6px 10px", fontSize: 12,
            background: colors.bg, color: colors.text,
            border: `1px solid ${colors.border}`, borderRadius: 6,
          }}
        />
      </div>
      <div style={{ display: "flex", gap: 4, marginBottom: 10 }}>
        {["all", "online", "offline"].map(f => (
          <button key={f} onClick={() => setMachineStatusFilter(f)} style={{
            flex: 1, padding: "4px 8px", fontSize: 11, textTransform: "capitalize",
            borderRadius: 6, border: `1px solid ${colors.border}`,
            background: machineStatusFilter === f ? colors.accentDim + "44" : "transparent",
            color: machineStatusFilter === f ? colors.accent : colors.textMuted,
            cursor: "pointer",
          }}>{f}</button>
        ))}
      </div>
      {entries.length === 0 ? (
        <div style={{ padding: 20, textAlign: "center", color: colors.textDim, fontSize: 12 }}>
          No machines match.
        </div>
      ) : entries.map(([name, data]) => (
        <MachineCard
          key={name} name={name} data={data}
          hardware={fleetHardware.find(h => h.name === name)}
          selected={selectedMachine === name}
          onClick={() => setSelectedMachine(name)}
        />
      ))}
    </div>
  );

  const detailPane = (
    <div style={{
      background: colors.surface, border: `1px solid ${colors.border}`,
      borderRadius: 12, overflow: "auto", maxHeight: "calc(100vh - 200px)",
    }}>
      {isNarrow && selectedMachine && (
        <button onClick={() => setSelectedMachine(null)} style={{
          margin: 12, padding: "6px 12px", fontSize: 12,
          background: "transparent", color: colors.accent,
          border: `1px solid ${colors.border}`, borderRadius: 6, cursor: "pointer",
        }}>← Back to list</button>
      )}
      <MachineDetailPane
        name={selectedMachine} data={selectedData}
        hardware={selectedHardware} runners={runners} runs={runs}
      />
    </div>
  );

  if (isNarrow) {
    return (
      <div>
        <h2 style={{ fontSize: 18, fontWeight: 600, margin: "0 0 16px" }}>Machines</h2>
        {selectedMachine ? detailPane : listPane}
      </div>
    );
  }

  return (
    <div>
      <h2 style={{ fontSize: 18, fontWeight: 600, margin: "0 0 16px" }}>Machines</h2>
      <div style={{ display: "grid", gridTemplateColumns: "320px 1fr", gap: 16 }}>
        {listPane}
        {detailPane}
      </div>
    </div>
  );
}

function RemediationPanel({
  runs,
  remediationConfig,
  remediationWorkflows,
  remediationLoading,
  remediationError,
  remediationProvider,
  setRemediationProvider,
  remediationPlan,
  remediationDispatchState,
  remediationSelectedRunId,
  setRemediationSelectedRunId,
  onRefresh,
  onSavePolicy,
  onPreview,
  onDispatch,
}) {
  const fallbackProviderOrder = ["jules_api", "codex_cli", "claude_code_cli", "ollama_local", "cline"];
  const failedRuns = runs.filter(run => run.conclusion === "failure");
  const selectedRun = failedRuns.find(run => String(run.id) === String(remediationSelectedRunId)) || failedRuns[0] || null;
  const policy = remediationConfig?.policy || {};
  const providers = remediationConfig?.providers || {};
  const availability = remediationConfig?.availability || {};
  const workflowEntries = remediationWorkflows?.workflows || [];
  const controlTowerSummary = remediationWorkflows?.control_tower_summary || "";
  const providerEntries = Object.keys(providers).length
    ? Object.entries(providers)
    : fallbackProviderOrder.map(providerId => [providerId, { label: providerId, notes: "" }]);
  const accepted = remediationPlan?.decision?.accepted;
  const maxSameFailureAttempts = policy.max_same_failure_attempts ?? 3;
  const [draftRules, setDraftRules] = useState(policy.workflow_type_rules || {});
  const [savingPolicy, setSavingPolicy] = useState(false);

  useEffect(() => {
    setDraftRules(policy.workflow_type_rules || {});
  }, [remediationConfig, policy.workflow_type_rules]);

  const updateRule = (workflowType, field, value) => {
    setDraftRules(prev => ({
      ...prev,
      [workflowType]: {
        ...(prev[workflowType] || {}),
        [field]: value,
      },
    }));
  };

  const savePolicy = async () => {
    setSavingPolicy(true);
    try {
      await onSavePolicy({
        ...policy,
        workflow_type_rules: draftRules,
      });
    } finally {
      setSavingPolicy(false);
    }
  };

  return (
    <>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 20, gap: 12, flexWrap: "wrap" }}>
        <div>
          <h2 style={{ fontSize: 18, fontWeight: 600, margin: 0 }}>Agent Remediation</h2>
          <div style={{ fontSize: 13, color: colors.textMuted, marginTop: 4 }}>
            Preview and dispatch CI remediation through Jules, Codex CLI, Claude Code CLI, and future local providers.
          </div>
        </div>
        <button
          onClick={onRefresh}
          style={{
            display: "flex", alignItems: "center", gap: 6,
            padding: "8px 14px", borderRadius: 10, border: `1px solid ${colors.border}`,
            background: colors.surface, color: colors.textMuted, cursor: "pointer", fontSize: 13,
          }}
        >
          <RefreshCw size={14} /> Refresh controls
        </button>
      </div>

      {remediationError && (
        <div style={{
          marginBottom: 16, padding: "10px 12px", borderRadius: 10,
          background: colors.redBg, color: colors.red, border: `1px solid ${colors.red}44`,
          fontSize: 13,
        }}>
          {remediationError}
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "minmax(320px, 420px) 1fr", gap: 20 }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div style={{
            background: colors.surface, border: `1px solid ${colors.border}`,
            borderRadius: 12, padding: 18,
          }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, marginBottom: 10 }}>
              <div>
                <div style={{ fontSize: 15, fontWeight: 600 }}>Automatic remediation configuration</div>
                <div style={{ fontSize: 12, color: colors.textMuted, marginTop: 4 }}>
                  Workflow Type Routing lets simple failures auto-dispatch while complex failures can stay manual until reviewed.
                </div>
              </div>
              <button
                onClick={savePolicy}
                disabled={savingPolicy || remediationLoading}
                style={{
                  padding: "8px 12px", borderRadius: 8, border: `1px solid ${colors.accent}`,
                  background: colors.accentDim + "22", color: colors.accent, cursor: "pointer",
                  opacity: (savingPolicy || remediationLoading) ? 0.5 : 1, fontWeight: 600,
                }}
              >
                {savingPolicy ? "Saving…" : "Save routing"}
              </button>
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              <div style={{ fontSize: 13, fontWeight: 600, color: colors.text }}>Workflow Type Routing</div>
              {Object.entries(draftRules).map(([workflowType, rule]) => (
                <div key={workflowType} style={{
                  background: colors.bg, border: `1px solid ${colors.border}`,
                  borderRadius: 8, padding: 12, display: "grid", gridTemplateColumns: "1.2fr 1fr 1fr", gap: 10,
                }}>
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 600, color: colors.text }}>{rule.label || workflowType}</div>
                    <div style={{ fontSize: 11, color: colors.textDim, marginTop: 4 }}>
                      {(rule.match_terms || []).join(", ") || "fallback"}
                    </div>
                  </div>
                  <label style={{ fontSize: 12, color: colors.textDim }}>
                    Dispatch mode
                    <select
                      value={rule.dispatch_mode || "manual"}
                      onChange={e => updateRule(workflowType, "dispatch_mode", e.target.value)}
                      style={{
                        width: "100%", marginTop: 6, padding: "9px 10px",
                        borderRadius: 8, border: `1px solid ${colors.border}`,
                        background: colors.surface, color: colors.text,
                      }}
                    >
                      <option value="auto">Auto</option>
                      <option value="manual">Manual</option>
                    </select>
                  </label>
                  <label style={{ fontSize: 12, color: colors.textDim }}>
                    Provider
                    <select
                      value={rule.provider_id || policy.default_provider || remediationProvider}
                      onChange={e => updateRule(workflowType, "provider_id", e.target.value)}
                      style={{
                        width: "100%", marginTop: 6, padding: "9px 10px",
                        borderRadius: 8, border: `1px solid ${colors.border}`,
                        background: colors.surface, color: colors.text,
                      }}
                    >
                      {providerEntries.map(([providerId, provider]) => (
                        <option key={`${workflowType}-${providerId}`} value={providerId}>
                          {provider.label}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
              ))}
            </div>
          </div>

          <div style={{
            background: colors.surface, border: `1px solid ${colors.border}`,
            borderRadius: 12, padding: 18,
          }}>
            <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 10 }}>Dispatch Context</div>
            <div style={{ fontSize: 12, color: colors.textMuted, marginBottom: 12 }}>
              Loop guard: the same failure will stop auto-dispatch after {maxSameFailureAttempts} times in a row.
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              <label style={{ fontSize: 12, color: colors.textDim }}>
                Failed run
                <select
                  value={selectedRun ? String(selectedRun.id) : ""}
                  onChange={e => setRemediationSelectedRunId(e.target.value)}
                  style={{
                    width: "100%", marginTop: 6, padding: "10px 12px",
                    borderRadius: 8, border: `1px solid ${colors.border}`,
                    background: colors.bg, color: colors.text,
                  }}
                >
                  {failedRuns.length === 0 && <option value="">No failed runs available</option>}
                  {failedRuns.map(run => (
                    <option key={run.id} value={String(run.id)}>
                      {`${run.repository?.name || "repo"} · ${run.name} · ${run.head_branch}`}
                    </option>
                  ))}
                </select>
              </label>

              <label style={{ fontSize: 12, color: colors.textDim }}>
                Provider
                <select
                  value={remediationProvider}
                  onChange={e => setRemediationProvider(e.target.value)}
                  style={{
                    width: "100%", marginTop: 6, padding: "10px 12px",
                    borderRadius: 8, border: `1px solid ${colors.border}`,
                    background: colors.bg, color: colors.text,
                  }}
                >
                  {(Object.keys(providers).length ? Object.entries(providers) : fallbackProviderOrder.map(providerId => [providerId, { label: providerId, notes: "" }])).map(([providerId, provider]) => (
                    <option key={providerId} value={providerId}>
                      {provider.label}
                    </option>
                  ))}
                </select>
              </label>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                <button
                  onClick={() => onPreview(selectedRun)}
                  disabled={!selectedRun || remediationLoading}
                  style={{
                    padding: "10px 12px", borderRadius: 8, border: `1px solid ${colors.accent}`,
                    background: colors.accentDim + "22", color: colors.accent, cursor: "pointer",
                    opacity: (!selectedRun || remediationLoading) ? 0.5 : 1, fontWeight: 600,
                  }}
                >
                  Preview plan
                </button>
                <button
                  onClick={() => onDispatch(selectedRun)}
                  disabled={!selectedRun || remediationLoading || accepted !== true}
                  style={{
                    padding: "10px 12px", borderRadius: 8, border: `1px solid ${colors.green}`,
                    background: colors.greenDim + "22", color: colors.green, cursor: "pointer",
                    opacity: (!selectedRun || remediationLoading || accepted !== true) ? 0.5 : 1, fontWeight: 600,
                  }}
                >
                  Dispatch agent
                </button>
              </div>
            </div>
          </div>

          <div style={{
            background: colors.surface, border: `1px solid ${colors.border}`,
            borderRadius: 12, padding: 18,
          }}>
            <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 10 }}>Providers</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {(Object.keys(providers).length ? Object.entries(providers) : fallbackProviderOrder.map(providerId => [providerId, { label: providerId, notes: "" }])).map(([providerId, provider]) => {
                const state = availability[providerId];
                const tone = state?.available ? colors.green : colors.orange;
                return (
                  <div key={providerId} style={{
                    padding: 10, borderRadius: 8, background: colors.bg, border: `1px solid ${colors.border}`,
                  }}>
                    <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                      <span style={{ fontSize: 13, fontWeight: 600 }}>{provider.label}</span>
                      <span style={{ fontSize: 11, color: tone }}>{state?.status || "unknown"}</span>
                    </div>
                    <div style={{ fontSize: 12, color: colors.textMuted, marginTop: 4 }}>{provider.notes}</div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div style={{
            background: colors.surface, border: `1px solid ${colors.border}`,
            borderRadius: 12, padding: 18,
          }}>
            <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 10 }}>Plan Preview</div>
            {!remediationPlan && (
              <div style={{ fontSize: 13, color: colors.textMuted }}>
                Select a failed run, choose a provider, and preview the plan before dispatch.
              </div>
            )}
            {remediationPlan && (
              <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                  <Badge status={accepted ? "success" : "failure"} label={accepted ? "dispatch allowed" : "blocked"} />
                  <span style={{ fontSize: 12, color: colors.textMuted }}>
                    {remediationPlan.decision?.reason}
                  </span>
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8 }}>
                  <div style={{ background: colors.bg, border: `1px solid ${colors.border}`, borderRadius: 8, padding: 10 }}>
                    <div style={{ fontSize: 11, color: colors.textDim, marginBottom: 4 }}>Fingerprint</div>
                    <div style={{ fontSize: 12, color: colors.textMuted, wordBreak: "break-word" }}>{remediationPlan.decision?.fingerprint || "—"}</div>
                  </div>
                  <div style={{ background: colors.bg, border: `1px solid ${colors.border}`, borderRadius: 8, padding: 10 }}>
                    <div style={{ fontSize: 11, color: colors.textDim, marginBottom: 4 }}>Attempts</div>
                    <div style={{ fontSize: 12, color: colors.textMuted }}>
                      {remediationPlan.decision?.attempt_count ?? 0} / {maxSameFailureAttempts}
                    </div>
                  </div>
                  <div style={{ background: colors.bg, border: `1px solid ${colors.border}`, borderRadius: 8, padding: 10 }}>
                    <div style={{ fontSize: 11, color: colors.textDim, marginBottom: 4 }}>Provider</div>
                    <div style={{ fontSize: 12, color: colors.textMuted }}>{remediationPlan.decision?.provider_id || remediationProvider}</div>
                  </div>
                </div>
                <pre style={{
                  margin: 0, padding: 12, borderRadius: 8, background: colors.bg,
                  border: `1px solid ${colors.border}`, color: colors.textMuted,
                  fontSize: 12, whiteSpace: "pre-wrap", maxHeight: 260, overflow: "auto",
                }}>
                  {remediationPlan.decision?.prompt_preview || "(no prompt preview returned)"}
                </pre>
                {remediationDispatchState && (
                  <div style={{ fontSize: 12, color: remediationDispatchState.error ? colors.red : colors.green }}>
                    {remediationDispatchState.error || remediationDispatchState.note}
                  </div>
                )}
              </div>
            )}
          </div>

          <div style={{
            background: colors.surface, border: `1px solid ${colors.border}`,
            borderRadius: 12, padding: 18,
          }}>
            <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 10 }}>Jules Workflow Health</div>
            {controlTowerSummary && (
              <div style={{
                marginBottom: 12, padding: "10px 12px", borderRadius: 8,
                background: colors.yellowBg, color: colors.yellow, border: `1px solid ${colors.yellow}44`,
                fontSize: 12,
              }}>
                {controlTowerSummary}
              </div>
            )}
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {workflowEntries.map(entry => (
                <div key={entry.workflow_file} style={{
                  background: colors.bg, border: `1px solid ${colors.border}`,
                  borderRadius: 8, padding: 12,
                }}>
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center" }}>
                    <div style={{ fontSize: 13, fontWeight: 600 }}>{entry.workflow_name}</div>
                    <Badge
                      status={entry.issues?.length ? "failure" : "success"}
                      label={entry.issues?.length ? `${entry.issues.length} issue${entry.issues.length === 1 ? "" : "s"}` : "healthy"}
                    />
                  </div>
                  <div style={{ fontSize: 12, color: colors.textDim, marginTop: 6 }}>
                    manual dispatch: {String(entry.manual_dispatch)} · scheduled: {String(entry.scheduled)} · workflow_run: {String(entry.workflow_run_trigger)}
                  </div>
                  {entry.issues?.length > 0 && (
                    <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 6 }}>
                      {entry.issues.map(issue => (
                        <div key={issue} style={{ fontSize: 12, color: colors.textMuted }}>
                          {issue}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}

// ─── Main Dashboard ──────────────────────────────────────────────────────────
export default function RunnerDashboard() {
  const [runners, setRunners] = useState([]);
  const [runs, setRuns] = useState([]);
  const [stats, setStats] = useState({});
  const [queue, setQueue] = useState({});
  const [tab, setTab] = useState("overview");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [toggling, setToggling] = useState(false);
  const [scaling, setScaling] = useState(false);
  const [lastRefresh, setLastRefresh] = useState(null);
  const [runFilter, setRunFilter] = useState("all");
  const [sortField, setSortField] = useState("updated_at");
  const [sortDir, setSortDir] = useState("desc");
  const [fleetStatus, setFleetStatus] = useState({});
  const [fleetHardware, setFleetHardware] = useState([]);
  const [selectedMachine, setSelectedMachine] = useState(null);
  const [machineSearch, setMachineSearch] = useState("");
  const [machineStatusFilter, setMachineStatusFilter] = useState("all");
  const [deploymentDrift, setDeploymentDrift] = useState(null);
  const [updateSignal, setUpdateSignal] = useState(null);
  const [matlabHealth, setMatlabHealth] = useState(null);
  const [remediationConfig, setRemediationConfig] = useState(null);
  const [remediationWorkflows, setRemediationWorkflows] = useState(null);
  const [remediationLoading, setRemediationLoading] = useState(false);
  const [remediationError, setRemediationError] = useState(null);
  const [remediationProvider, setRemediationProvider] = useState("jules_api");
  const [remediationPlan, setRemediationPlan] = useState(null);
  const [remediationDispatchState, setRemediationDispatchState] = useState(null);
  const [remediationSelectedRunId, setRemediationSelectedRunId] = useState("");
  const pollRef = useRef(null);

  // ── Demo data for standalone preview ─────────────────────────────────────
  const useDemoData = useCallback(() => {
    const demoRunners = [
      { id: 1, name: "d-sorg-local-ControlTower-1", status: "online", busy: false, os: "Linux", labels: [{ id: 1, name: "self-hosted" }, { id: 2, name: "Linux" }, { id: 3, name: "X64" }, { id: 4, name: "d-sorg-fleet" }, { id: 5, name: "d-sorg-fleet-4core" }] },
      { id: 2, name: "d-sorg-local-ControlTower-2", status: "online", busy: true, os: "Linux", labels: [{ id: 1, name: "self-hosted" }, { id: 2, name: "Linux" }, { id: 3, name: "X64" }, { id: 4, name: "d-sorg-fleet" }, { id: 5, name: "d-sorg-fleet-4core" }] },
      { id: 3, name: "d-sorg-local-ControlTower-3", status: "online", busy: false, os: "Linux", labels: [{ id: 1, name: "self-hosted" }, { id: 2, name: "Linux" }, { id: 3, name: "X64" }, { id: 4, name: "d-sorg-fleet" }, { id: 5, name: "d-sorg-fleet-4core" }] },
      { id: 4, name: "d-sorg-local-ControlTower-4", status: "offline", busy: false, os: "Linux", labels: [{ id: 1, name: "self-hosted" }, { id: 2, name: "Linux" }, { id: 3, name: "X64" }, { id: 4, name: "d-sorg-fleet" }, { id: 5, name: "d-sorg-fleet-4core" }] },
    ];
    const demoRuns = [
      { id: 1, name: "CI Standard", head_branch: "main", status: "completed", conclusion: "success", event: "push", run_number: 42, run_attempt: 1, head_sha: "a1b2c3d4e5f6", created_at: new Date(Date.now() - 300000).toISOString(), run_started_at: new Date(Date.now() - 300000).toISOString(), updated_at: new Date(Date.now() - 120000).toISOString(), html_url: "#", repository: { name: "Controls" } },
      { id: 2, name: "CI Standard", head_branch: "ci/fix-runner-token", status: "completed", conclusion: "failure", event: "push", run_number: 87, run_attempt: 1, head_sha: "f6e5d4c3b2a1", created_at: new Date(Date.now() - 900000).toISOString(), run_started_at: new Date(Date.now() - 900000).toISOString(), updated_at: new Date(Date.now() - 600000).toISOString(), html_url: "#", repository: { name: "AffineDrift" } },
      { id: 3, name: "Quarto PDF Render", head_branch: "main", status: "in_progress", conclusion: null, event: "push", run_number: 15, run_attempt: 1, head_sha: "1a2b3c4d5e6f", created_at: new Date(Date.now() - 60000).toISOString(), run_started_at: new Date(Date.now() - 60000).toISOString(), updated_at: new Date().toISOString(), html_url: "#", repository: { name: "Gasification_Model" } },
      { id: 4, name: "CI Standard", head_branch: "feature/new-model", status: "completed", conclusion: "success", event: "pull_request", run_number: 31, run_attempt: 1, head_sha: "9f8e7d6c5b4a", created_at: new Date(Date.now() - 3600000).toISOString(), run_started_at: new Date(Date.now() - 3600000).toISOString(), updated_at: new Date(Date.now() - 3300000).toISOString(), html_url: "#", repository: { name: "Golf_GAAI_Sandbox" } },
      { id: 5, name: "Heavy Integration Tests", head_branch: "main", status: "completed", conclusion: "cancelled", event: "workflow_dispatch", run_number: 8, run_attempt: 1, head_sha: "0a1b2c3d4e5f", created_at: new Date(Date.now() - 7200000).toISOString(), run_started_at: new Date(Date.now() - 7200000).toISOString(), updated_at: new Date(Date.now() - 7100000).toISOString(), html_url: "#", repository: { name: "Tools" } },
      { id: 6, name: "CI Standard", head_branch: "main", status: "completed", conclusion: "success", event: "push", run_number: 22, run_attempt: 1, head_sha: "abcdef123456", created_at: new Date(Date.now() - 1800000).toISOString(), run_started_at: new Date(Date.now() - 1800000).toISOString(), updated_at: new Date(Date.now() - 1500000).toISOString(), html_url: "#", repository: { name: "Drake_Models" } },
    ];
    setRunners(demoRunners);
    setRuns(demoRuns);
    setLoading(false);
    setLastRefresh(new Date());
  }, []);

  // ── Fetch data ───────────────────────────────────────────────────────────
  const fetchOptionalData = useCallback(async () => {
    try {
      const [fleetRes, statsRes, queueRes, hardwareRes, driftRes, matlabRes] = await Promise.all([
        fetch(`${API_BASE}/fleet/status`).catch(() => null),
        fetch(`${API_BASE}/stats`).catch(() => null),
        fetch(`${API_BASE}/queue`).catch(() => null),
        fetch(`${API_BASE}/fleet/hardware`).catch(() => null),
        fetch(`${API_BASE}/deployment/drift`).catch(() => null),
        fetch(`${API_BASE}/runners/matlab`).catch(() => null),
      ]);
      if (fleetRes && fleetRes.ok) {
        setFleetStatus(await fleetRes.json());
      }
      if (statsRes && statsRes.ok) {
        setStats(await statsRes.json());
      }
      if (queueRes && queueRes.ok) {
        setQueue(await queueRes.json());
      }
      if (hardwareRes && hardwareRes.ok) {
        const hardwareData = await hardwareRes.json();
        setFleetHardware(hardwareData.machines || []);
      }
      if (driftRes && driftRes.ok) {
        setDeploymentDrift(await driftRes.json());
      }
      if (matlabRes && matlabRes.ok) {
        setMatlabHealth(await matlabRes.json());
      }
    } catch {
      // Optional aggregate panels must not block core runner and run refreshes.
    }
  }, []);

  const fetchData = useCallback(async () => {
    fetchOptionalData();
    try {
      const [runnersRes, runsRes] = await Promise.all([
        fetch(`${API_BASE}/runners`),
        fetch(`${API_BASE}/runs/enriched`),
      ]);
      if (!runnersRes.ok || !runsRes.ok) throw new Error("API error");
      const runnersData = await runnersRes.json();
      const runsData = await runsRes.json();
      setRunners(runnersData.runners || []);
      setRuns(runsData.workflow_runs || []);
      setError(null);
      setLastRefresh(new Date());
    } catch (err) {
      if (!runners.length) useDemoData();
      setError("Backend not connected — showing demo data. Ensure HUB_URL is set if this is a node.");
    } finally {
      setLoading(false);
    }
  }, [fetchOptionalData, runners.length, useDemoData]);

  const buildRemediationContext = useCallback((run) => {
    if (!run) return null;
    const branch = run.head_branch || "";
    const repoName = run.repository?.name || "";
    const workflowName = run.name || run.workflow_name || "CI Standard";
    return {
      repository: repoName,
      workflow_name: workflowName,
      branch,
      run_id: run.id,
      failure_reason: `${workflowName} failed for ${repoName} on ${branch}`,
      protected_branch: branch === "main" || branch === "master",
      attempts: [],
    };
  }, []);

  const fetchRemediationData = useCallback(async () => {
    setRemediationLoading(true);
    try {
      const [configRes, workflowsRes] = await Promise.all([
        fetch(`${API_BASE}/agent-remediation/config`),
        fetch(`${API_BASE}/agent-remediation/workflows`),
      ]);
      if (!configRes.ok || !workflowsRes.ok) throw new Error("Failed remediation API request");
      const configData = await configRes.json();
      const workflowsData = await workflowsRes.json();
      setRemediationConfig(configData);
      setRemediationWorkflows(workflowsData);
      setRemediationProvider(configData.policy?.default_provider || "jules_api");
      setRemediationError(null);
    } catch (err) {
      setRemediationError("Failed to load remediation controls from the dashboard backend.");
    } finally {
      setRemediationLoading(false);
    }
  }, []);

  const saveRemediationPolicy = useCallback(async (nextPolicy) => {
    setRemediationLoading(true);
    try {
      const res = await fetch(`${API_BASE}/agent-remediation/config`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ policy: nextPolicy }),
      });
      const payload = await res.json();
      if (!res.ok) throw new Error(payload.detail || "Failed to save remediation policy.");
      setRemediationConfig(payload);
      setRemediationProvider(payload.policy?.default_provider || "jules_api");
      setRemediationError(null);
      return payload;
    } catch (err) {
      setRemediationError(err.message || "Failed to save remediation policy.");
      throw err;
    } finally {
      setRemediationLoading(false);
    }
  }, []);

  const previewRemediationPlan = useCallback(async (run) => {
    const context = buildRemediationContext(run);
    if (!context) {
      setRemediationError("Select a failed run before previewing remediation.");
      return;
    }
    setRemediationLoading(true);
    setRemediationDispatchState(null);
    try {
      const res = await fetch(`${API_BASE}/agent-remediation/plan`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...context, provider_override: remediationProvider }),
      });
      const payload = await res.json();
      if (!res.ok) throw new Error(payload.detail || "Failed to preview remediation.");
      setRemediationPlan(payload);
      setRemediationError(null);
    } catch (err) {
      setRemediationPlan(null);
      setRemediationError(err.message || "Failed to preview remediation.");
    } finally {
      setRemediationLoading(false);
    }
  }, [buildRemediationContext, remediationProvider]);

  const dispatchRemediation = useCallback(async (run) => {
    const context = buildRemediationContext(run);
    if (!context) {
      setRemediationError("Select a failed run before dispatching remediation.");
      return;
    }
    setRemediationLoading(true);
    try {
      const res = await fetch(`${API_BASE}/agent-remediation/dispatch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...context, provider: remediationProvider }),
      });
      const payload = await res.json();
      if (!res.ok) throw new Error(payload.detail || "Failed to dispatch remediation.");
      setRemediationDispatchState({
        note: `Dispatched ${payload.provider} through ${payload.workflow}.`,
      });
      setRemediationError(null);
      setRemediationPlan(prev => prev || { decision: { provider_id: remediationProvider } });
    } catch (err) {
      setRemediationDispatchState({ error: err.message || "Failed to dispatch remediation." });
      setRemediationError(err.message || "Failed to dispatch remediation.");
    } finally {
      setRemediationLoading(false);
    }
  }, [buildRemediationContext, remediationProvider]);

  useEffect(() => {
    fetchData();
    pollRef.current = setInterval(fetchData, POLL_INTERVAL);
    return () => clearInterval(pollRef.current);
  }, [fetchData]);

  useEffect(() => {
    if (tab === "remediation" && !remediationConfig && !remediationLoading) {
      fetchRemediationData();
    }
  }, [tab, remediationConfig, remediationLoading, fetchRemediationData]);

  useEffect(() => {
    const failedRuns = runs.filter(run => run.conclusion === "failure");
    if (!remediationSelectedRunId && failedRuns.length > 0) {
      setRemediationSelectedRunId(String(failedRuns[0].id));
    }
  }, [runs, remediationSelectedRunId]);

  // ── Toggle runner ────────────────────────────────────────────────────────
  const handleToggle = async (runner) => {
    setToggling(true);
    const action = runner.status === "online" ? "stop" : "start";
    try {
      await fetch(`${API_BASE}/runners/${runner.id}/${action}`, { method: "POST" });
      setTimeout(fetchData, 2000);
    } catch {
      setError(`Failed to ${action} runner. Is the backend running?`);
    } finally {
      setTimeout(() => setToggling(false), 2000);
    }
  };

  // ── Scale fleet ──────────────────────────────────────────────────────────
  const handleScale = async (direction) => {
    setScaling(true);
    try {
      await fetch(`${API_BASE}/fleet/${direction}`, { method: "POST" });
      setTimeout(fetchData, 3000);
    } catch {
      setError("Failed to scale fleet. Is the backend running?");
    } finally {
      setTimeout(() => setScaling(false), 3000);
    }
  };

  // ── Request a drift-triggered node update (notify-only) ───────────────────
  const handleRequestUpdate = async (nodeName) => {
    setUpdateSignal({ node: nodeName, state: "pending" });
    try {
      const res = await fetch(`${API_BASE}/deployment/update-signal`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ node: nodeName, reason: "dashboard-ui" }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setUpdateSignal({ node: nodeName, state: "sent" });
      setTimeout(() => setUpdateSignal(null), 4000);
    } catch {
      setUpdateSignal({ node: nodeName, state: "error" });
      setTimeout(() => setUpdateSignal(null), 4000);
    }
  };

  // ── Computed stats ───────────────────────────────────────────────────────
  const online = runners.filter(r => r.status === "online").length;
  const busy = runners.filter(r => r.busy).length;
  const recentSuccesses = runs.filter(r => r.conclusion === "success").length;
  const recentFailures = runs.filter(r => r.conclusion === "failure").length;
  const completedRuns = runs.filter(r => r.conclusion).length;
  const successRate = stats.success_rate ?? (completedRuns ? Math.round((recentSuccesses / completedRuns) * 100) : 0);
  const openPrs = stats.org_open_prs ?? "—";
  const openIssues = stats.org_open_issues ?? "—";
  const queuedWorkflows = stats.queued ?? queue.queued_count ?? 0;
  const runningWorkflows = stats.in_progress ?? queue.in_progress_count ?? runs.filter(r => r.status === "in_progress").length;
  const machinesTotal = stats.machines_total ?? Object.keys(fleetStatus).length;
  const machinesOnline = stats.machines_online ?? Object.values(fleetStatus).filter(m => m.status !== "offline").length;

  // ── Filtered & sorted runs ───────────────────────────────────────────────
  let filteredRuns = runs;
  if (runFilter !== "all") {
    filteredRuns = runs.filter(r => (r.conclusion || r.status) === runFilter);
  }
  filteredRuns = [...filteredRuns].sort((a, b) => {
    const aVal = a[sortField] || "";
    const bVal = b[sortField] || "";
    return sortDir === "desc" ? bVal.localeCompare(aVal) : aVal.localeCompare(bVal);
  });

  if (loading) {
    return (
      <div style={{
        background: colors.bg, minHeight: "100vh", display: "flex",
        alignItems: "center", justifyContent: "center", color: colors.textMuted,
      }}>
        <RefreshCw size={24} style={{ animation: "spin 1s linear infinite" }} />
        <span style={{ marginLeft: 12, fontSize: 16 }}>Loading runners...</span>
      </div>
    );
  }

  return (
    <div style={{ background: colors.bg, minHeight: "100vh", color: colors.text, fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif" }}>
      <style>{`
        @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
        .spin { animation: spin 1s linear infinite; }
        ::-webkit-scrollbar { width: 8px; }
        ::-webkit-scrollbar-track { background: ${colors.bg}; }
        ::-webkit-scrollbar-thumb { background: ${colors.border}; border-radius: 4px; }
        ::-webkit-scrollbar-thumb:hover { background: ${colors.borderLight}; }
      `}</style>

      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <header style={{
        borderBottom: `1px solid ${colors.border}`,
        padding: "16px 32px", display: "flex",
        justifyContent: "space-between", alignItems: "center",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <div style={{
            width: 40, height: 40, borderRadius: 12,
            background: `linear-gradient(135deg, ${colors.accentDim}, ${colors.purple})`,
            display: "flex", alignItems: "center", justifyContent: "center",
          }}>
            <Zap size={20} color="#fff" />
          </div>
          <div>
            <h1 style={{ fontSize: 20, fontWeight: 700, margin: 0, letterSpacing: "-0.3px" }}>
              D-sorganization Fleet
            </h1>
            <span style={{ fontSize: 12, color: colors.textDim }}>Self-Hosted Runner Dashboard</span>
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
          {error && (
            <div style={{
              display: "flex", alignItems: "center", gap: 6,
              padding: "6px 12px", borderRadius: 8, fontSize: 12,
              background: colors.yellowBg, color: colors.yellow,
              border: `1px solid ${colors.yellow}33`, maxWidth: 400,
            }}>
              <AlertTriangle size={14} />
              <span>{error}</span>
            </div>
          )}
          <span style={{ fontSize: 12, color: colors.textDim }}>
            {lastRefresh ? `Updated ${timeAgo(lastRefresh.toISOString())}` : ""}
          </span>
          <button
            onClick={() => { setLoading(true); fetchData(); }}
            style={{
              display: "flex", alignItems: "center", gap: 6,
              padding: "8px 14px", borderRadius: 10, border: `1px solid ${colors.border}`,
              background: colors.surface, color: colors.textMuted,
              cursor: "pointer", fontSize: 13, transition: "all 0.2s",
            }}
          >
            <RefreshCw size={14} /> Refresh
          </button>
        </div>
      </header>

      {/* ── Navigation ─────────────────────────────────────────────────────── */}
      <nav style={{
        borderBottom: `1px solid ${colors.border}`,
        padding: "0 32px", display: "flex", gap: 4,
      }}>
        <TabButton active={tab === "overview"} icon={BarChart3} label="Overview" onClick={() => setTab("overview")} />
        <TabButton active={tab === "remediation"} icon={Settings} label="Remediation" onClick={() => setTab("remediation")} count={runs.filter(r => r.conclusion === "failure").length} />
        <TabButton active={tab === "queue"} icon={Clock} label="Queue" onClick={() => setTab("queue")} count={queue.total || 0} />
        <TabButton active={tab === "machines"} icon={Cpu} label="Machines" onClick={() => setTab("machines")} count={Object.keys(fleetStatus).length} />
        <TabButton active={tab === "runners"} icon={Server} label="Runners" onClick={() => setTab("runners")} count={runners.length} />
        <TabButton active={tab === "history"} icon={History} label="Workflow History" onClick={() => setTab("history")} count={runs.length} />
        <div style={{ flex: 1 }} />
        <TabButton active={tab === "onboarding"} icon={Plus} label="Add Machine" onClick={() => setTab("onboarding")} />
      </nav>

      {/* ── Content ────────────────────────────────────────────────────────── */}
      <main style={{ padding: "24px 32px", maxWidth: 1400, margin: "0 auto" }}>

        {/* ── Overview Tab ──────────────────────────────────────────────────── */}
        {tab === "overview" && (
          <>
            {/* Stats row */}
            <div style={{ display: "flex", gap: 16, marginBottom: 24, flexWrap: "wrap" }}>
              <StatCard icon={Server} label="Runners Online" value={`${stats.runners_online ?? online}/${stats.runners_total ?? runners.length}`} color={colors.green} subtitle={`${stats.runners_busy ?? busy} busy right now`} />
              <StatCard icon={Cpu} label="Machines Online" value={`${machinesOnline}/${machinesTotal}`} color={machinesOnline === machinesTotal ? colors.green : colors.yellow} subtitle="dashboard telemetry" />
              <StatCard icon={GitBranch} label="Open PRs" value={openPrs} color={colors.accent} subtitle="across org" />
              <StatCard icon={AlertTriangle} label="Open Issues" value={openIssues} color={colors.orange} subtitle="excluding PRs" />
              <StatCard icon={Clock} label="Queued Workflows" value={queuedWorkflows} color={queuedWorkflows > 0 ? colors.yellow : colors.green} subtitle="waiting for runners" />
              <StatCard icon={Activity} label="Running Workflows" value={runningWorkflows} color={runningWorkflows > 0 ? colors.yellow : colors.green} subtitle="in progress now" />
              <StatCard icon={CheckCircle} label="Success Rate" value={`${successRate}%`} color={successRate >= 90 ? colors.green : successRate >= 70 ? colors.yellow : colors.red} subtitle={`${stats.runs_success ?? recentSuccesses}/${stats.runs_completed ?? completedRuns} recent completed runs passed`} />
              <StatCard icon={XCircle} label="Recent Failures" value={stats.runs_failure ?? recentFailures} color={colors.red} subtitle="sampled recent runs" />
            </div>

            {/* Fleet control + recent runs */}
            <div style={{ display: "grid", gridTemplateColumns: "360px 1fr", gap: 20 }}>
              <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
                <FleetScaler runners={runners} onScale={handleScale} scaling={scaling} />
                
                {/* Hardware Metrics Summary */}
                {Object.keys(fleetStatus).length > 0 && (
                  <div style={{
                    background: colors.surface, border: `1px solid ${colors.border}`,
                    borderRadius: 12, padding: 20,
                  }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 16 }}>
                      <Activity size={18} color={colors.accent} />
                      <span style={{ fontWeight: 600, fontSize: 15, color: colors.text }}>Hardware Capacity</span>
                      {deploymentDrift && deploymentDrift.drift && (
                        <span
                          title={deploymentDrift.message}
                          style={{
                            marginLeft: "auto",
                            padding: "3px 8px",
                            borderRadius: 8,
                            fontSize: 11,
                            fontWeight: 600,
                            color: deploymentDrift.dirty ? colors.red : colors.orange,
                            border: `1px solid ${deploymentDrift.dirty ? colors.red : colors.orange}`,
                            background: (deploymentDrift.dirty ? colors.red : colors.orange) + "22",
                          }}
                        >
                          {deploymentDrift.dirty
                            ? `Dirty deploy · ${deploymentDrift.current}`
                            : `Update available · ${deploymentDrift.current} → ${deploymentDrift.expected}`}
                        </span>
                      )}
                    </div>
                    {deploymentDrift && deploymentDrift.update_available && (
                      <div style={{
                        marginBottom: 12,
                        padding: "10px 12px",
                        borderRadius: 10,
                        border: `1px solid ${colors.orange}`,
                        background: colors.orange + "18",
                        display: "flex",
                        flexDirection: "column",
                        gap: 8,
                      }}>
                        <span style={{ fontSize: 12, color: colors.text }}>
                          {deploymentDrift.message}
                        </span>
                        <button
                          onClick={() => {
                            if (window.confirm(`Signal update request for ${deploymentDrift.hostname || "this node"}? This does not SSH; it logs a notification for scheduled maintenance to pick up.`)) {
                              handleRequestUpdate(deploymentDrift.hostname || "local");
                            }
                          }}
                          style={{
                            alignSelf: "flex-start",
                            padding: "6px 12px",
                            borderRadius: 8,
                            border: `1px solid ${colors.orange}`,
                            background: colors.orange + "33",
                            color: colors.orange,
                            fontWeight: 600,
                            fontSize: 12,
                            cursor: "pointer",
                          }}
                        >
                          Update node
                        </button>
                        {updateSignal && updateSignal.node === (deploymentDrift.hostname || "local") && (
                          <span style={{ fontSize: 11, color: updateSignal.state === "error" ? colors.red : colors.textDim }}>
                            {updateSignal.state === "pending" && "Sending update signal…"}
                            {updateSignal.state === "sent" && "Update signal recorded. Scheduled maintenance will pick it up on its next run."}
                            {updateSignal.state === "error" && "Failed to record update signal — check backend logs."}
                          </span>
                        )}
                      </div>
                    )}
                    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                      {Object.entries(fleetStatus).map(([name, data]) => {
                        const hardware = fleetHardware.find(item => item.name === name);
                        const specs = hardware?.hardware_specs || data.hardware_specs || {};
                        const capacity = hardware?.workload_capacity || data.workload_capacity || {};
                        const isOffline = data.status === "offline";
                        const cpu = isOffline ? 0 : (data.cpu?.percent_1m_avg || data.cpu?.percent || 0);
                        const ram = isOffline ? 0 : (data.memory?.percent || 0);
                        const logical = specs.cpu_logical_cores || data.cpu?.cores_logical || "?";
                        const memoryGb = specs.memory_gb || data.memory?.total_gb || "?";
                        const tags = capacity.tags?.slice(0, 2).join(", ");
                        const reason = data.offline_reason || (isOffline ? "unknown" : null);
                        const reasonLabel = {
                          wsl_connection_lost: "WSL connection lost",
                          resource_monitoring: "Resource monitoring",
                          computer_offline: "Computer offline",
                          dashboard_unhealthy: "Dashboard unhealthy",
                          unknown: "Unknown",
                        }[reason] || reason;
                        return (
                          <div key={name}>
                            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, marginBottom: 4 }}>
                              <span style={{ color: isOffline ? colors.red : colors.textMuted }}>
                                {name} {data._role === "hub" && "⭐"}
                              </span>
                              <span style={{ color: colors.textDim }}>
                                {isOffline ? `Offline · ${reasonLabel}` : `${logical} core · ${memoryGb} GB`}
                              </span>
                            </div>
                            {tags && (
                              <div style={{ color: colors.textDim, fontSize: 11, marginBottom: 4 }}>
                                {tags}
                              </div>
                            )}
                            <div style={{ display: "flex", gap: 6, height: 6 }}>
                              <div style={{ flex: 1, background: colors.border, borderRadius: 3, overflow: "hidden" }}>
                                <div style={{ height: "100%", width: `${cpu}%`, background: cpu > 80 ? colors.red : colors.accent, transition: "width 0.3s" }} />
                              </div>
                              <div style={{ flex: 1, background: colors.border, borderRadius: 3, overflow: "hidden" }}>
                                <div style={{ height: "100%", width: `${ram}%`, background: ram > 80 ? colors.orange : colors.purple, transition: "width 0.3s" }} />
                              </div>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}
              </div>
              <div style={{
                background: colors.surface, border: `1px solid ${colors.border}`,
                borderRadius: 12, overflow: "hidden",
              }}>
                <div style={{
                  padding: "16px 20px", borderBottom: `1px solid ${colors.border}`,
                  display: "flex", alignItems: "center", justifyContent: "space-between",
                }}>
                  <span style={{ fontWeight: 600, fontSize: 15 }}>Recent Workflow Runs</span>
                  <button onClick={() => setTab("history")} style={{
                    background: "none", border: "none", color: colors.accent,
                    cursor: "pointer", fontSize: 13, fontWeight: 500,
                  }}>
                    View all <Eye size={13} style={{ verticalAlign: "middle", marginLeft: 4 }} />
                  </button>
                </div>
                {runs.slice(0, 5).map(run => <WorkflowRunRow key={run.id} run={run} />)}
                {runs.length === 0 && (
                  <div style={{ padding: 40, textAlign: "center", color: colors.textDim }}>
                    No workflow runs found
                  </div>
                )}
              </div>
            </div>
          </>
        )}

        {/* ── Machines Tab (master-detail) ──────────────────────────────────── */}
        {tab === "machines" && (
          <MachinesMasterDetail
            fleetStatus={fleetStatus}
            fleetHardware={fleetHardware}
            runners={runners}
            runs={runs}
            selectedMachine={selectedMachine}
            setSelectedMachine={setSelectedMachine}
            machineSearch={machineSearch}
            setMachineSearch={setMachineSearch}
            machineStatusFilter={machineStatusFilter}
            setMachineStatusFilter={setMachineStatusFilter}
          />
        )}

        {/* ── Runners Tab ───────────────────────────────────────────────────── */}
        {tab === "runners" && (
          <>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 20 }}>
              <h2 style={{ fontSize: 18, fontWeight: 600, margin: 0 }}>Runner Fleet</h2>
              <div style={{ display: "flex", gap: 8 }}>
                <button
                  onClick={() => handleScale("all-up")}
                  disabled={scaling || online === runners.length}
                  style={{
                    padding: "8px 16px", borderRadius: 10, border: `1px solid ${colors.greenDim}`,
                    background: colors.greenDim + "33", color: colors.green,
                    fontWeight: 600, fontSize: 13, cursor: "pointer",
                    opacity: (scaling || online === runners.length) ? 0.4 : 1,
                  }}
                >
                  <Power size={14} style={{ verticalAlign: "middle", marginRight: 6 }} />
                  Start All
                </button>
                <button
                  onClick={() => handleScale("all-down")}
                  disabled={scaling || online === 0}
                  style={{
                    padding: "8px 16px", borderRadius: 10, border: `1px solid ${colors.redDim}`,
                    background: colors.redDim + "33", color: colors.red,
                    fontWeight: 600, fontSize: 13, cursor: "pointer",
                    opacity: (scaling || online === 0) ? 0.4 : 1,
                  }}
                >
                  <PowerOff size={14} style={{ verticalAlign: "middle", marginRight: 6 }} />
                  Stop All
                </button>
              </div>
            </div>
            <MatlabRunnerPanel health={matlabHealth} />
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(340px, 1fr))", gap: 16 }}>
              {runners.map(r => (
                <RunnerCard key={r.id} runner={r} onToggle={handleToggle} toggling={toggling} />
              ))}
            </div>
          </>
        )}

        {/* ── History Tab ───────────────────────────────────────────────────── */}
        {tab === "history" && (
          <>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 20 }}>
              <h2 style={{ fontSize: 18, fontWeight: 600, margin: 0 }}>Workflow History</h2>
              <div style={{ display: "flex", gap: 8 }}>
                {["all", "success", "failure", "in_progress", "cancelled"].map(f => (
                  <button key={f} onClick={() => setRunFilter(f)} style={{
                    padding: "6px 14px", borderRadius: 8, border: `1px solid ${colors.border}`,
                    background: runFilter === f ? colors.accentDim + "44" : colors.surface,
                    color: runFilter === f ? colors.accent : colors.textMuted,
                    fontSize: 12, fontWeight: 500, cursor: "pointer", textTransform: "capitalize",
                  }}>
                    {f === "all" ? "All" : f === "in_progress" ? "Running" : f}
                  </button>
                ))}
              </div>
            </div>
            <div style={{
              background: colors.surface, border: `1px solid ${colors.border}`,
              borderRadius: 12, overflow: "hidden",
            }}>
              {/* Table header */}
              <div style={{
                display: "grid", gridTemplateColumns: "32px 2fr 1.2fr 100px 100px 90px 32px",
                gap: 12, padding: "10px 16px", borderBottom: `1px solid ${colors.border}`,
                fontSize: 11, color: colors.textDim, fontWeight: 600, textTransform: "uppercase",
                letterSpacing: "0.5px",
              }}>
                <div></div>
                <div style={{ cursor: "pointer", display: "flex", alignItems: "center", gap: 4 }}
                  onClick={() => { setSortField("name"); setSortDir(d => d === "asc" ? "desc" : "asc"); }}>
                  Workflow <ArrowUpDown size={10} />
                </div>
                <div>Repository</div>
                <div>Status</div>
                <div>Duration</div>
                <div style={{ cursor: "pointer", display: "flex", alignItems: "center", gap: 4 }}
                  onClick={() => { setSortField("updated_at"); setSortDir(d => d === "asc" ? "desc" : "asc"); }}>
                  Time <ArrowUpDown size={10} />
                </div>
                <div></div>
              </div>
              {filteredRuns.map(run => <WorkflowRunRow key={run.id} run={run} />)}
              {filteredRuns.length === 0 && (
                <div style={{ padding: 40, textAlign: "center", color: colors.textDim }}>
                  No runs match this filter
                </div>
              )}
            </div>
          </>
        )}

        {tab === "remediation" && (
          <RemediationPanel
            runs={runs}
            remediationConfig={remediationConfig}
            remediationWorkflows={remediationWorkflows}
            remediationLoading={remediationLoading}
            remediationError={remediationError}
            remediationProvider={remediationProvider}
            setRemediationProvider={setRemediationProvider}
            remediationPlan={remediationPlan}
            remediationDispatchState={remediationDispatchState}
            remediationSelectedRunId={remediationSelectedRunId}
            setRemediationSelectedRunId={setRemediationSelectedRunId}
            onRefresh={fetchRemediationData}
            onSavePolicy={saveRemediationPolicy}
            onPreview={previewRemediationPlan}
            onDispatch={dispatchRemediation}
          />
        )}

        {tab === "queue" && (
          <QueuePanel
            queue={queue}
            onRefresh={fetchOptionalData}
          />
        )}

        {/* ── Onboarding Tab ────────────────────────────────────────────────── */}
        {tab === "onboarding" && (
          <div style={{
            background: colors.surface, border: `1px solid ${colors.border}`,
            borderRadius: 12, overflow: "hidden", padding: 32, maxWidth: 800, margin: "0 auto"
          }}>
            <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 24 }}>
              <div style={{
                width: 48, height: 48, borderRadius: 12,
                background: colors.accentDim + "33", color: colors.accent,
                display: "flex", alignItems: "center", justifyContent: "center"
              }}>
                <Server size={24} />
              </div>
              <div>
                <h2 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Add Machine to Fleet</h2>
                <div style={{ fontSize: 13, color: colors.textMuted }}>Provision a new hardware node for the D-sorganization Control Tower.</div>
              </div>
            </div>

            <p style={{ color: colors.textDim, fontSize: 14, lineHeight: 1.6, marginBottom: 24 }}>
              Execute the following payload inside a fresh Ubuntu WSL container on the new machine. 
              The script will automatically detect the host's physical RAM using PowerShell interop, 
              recommend WSL memory configuration bumps, and intelligently provision parallel runners 
              based on available CPU cores.
            </p>

            <div style={{ background: colors.bg, borderRadius: 8, padding: 16, border: `1px solid ${colors.border}`, position: "relative" }}>
              <button 
                onClick={(e) => {
                  navigator.clipboard.writeText(e.target.nextElementSibling.innerText);
                  e.target.innerText = "Copied!";
                  setTimeout(() => e.target.innerText = "Copy", 2000);
                }}
                style={{
                  position: "absolute", top: 12, right: 12, background: colors.surface, 
                  border: `1px solid ${colors.border}`, color: colors.textMuted, 
                  padding: "4px 10px", borderRadius: 6, fontSize: 12, cursor: "pointer"
                }}
              >Copy</button>
              <pre style={{ margin: 0, color: colors.text, fontSize: 13, overflowX: "auto", fontFamily: "monospace", opacity: 0.9 }}>
<span style={{ color: colors.textDim }}>#!/bin/bash</span><br/>
<span style={{ color: colors.textDim }}># D-sorganization Fleet Onboarding Payload</span><br/>
<br/>
<span style={{ color: colors.accentDim }}>echo</span> <span style={{ color: colors.green }}>"🚀 Initializing Fleet Hardware Discovery..."</span><br/>
<span style={{ color: colors.accentDim }}>sudo</span> apt-get update <span style={{ color: colors.red }}>&&</span> <span style={{ color: colors.accentDim }}>sudo</span> apt-get install -y jq curl<br/>
<br/>
<span style={{ color: colors.textDim }}># Auto-detect hardware limits through WSL boundary</span><br/>
HOST_RAM=$(powershell.exe -Command <span style={{ color: colors.green }}>"(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory"</span> | tr -d <span style={{ color: colors.green }}>'\r'</span>)<br/>
HOST_RAM_GB=$((HOST_RAM / 1024 / 1024 / 1024))<br/>
CORES=$(nproc)<br/>
<span style={{ color: colors.accentDim }}>echo</span> <span style={{ color: colors.green }}>"📊 Detected Host Hardware: $HOST_RAM_GB GB RAM and $CORES CPU cores."</span><br/>
<br/>
<span style={{ color: colors.textDim }}># Calculate optimal runner limits</span><br/>
OPT_RUNNERS=$((CORES / 2))<br/>
<br/>
<span style={{ color: colors.accentDim }}>echo</span> <span style={{ color: colors.green }}>"⚙️ We recommend provisioning $OPT_RUNNERS runners on this hardware."</span><br/>
<span style={{ color: colors.accentDim }}>echo</span> <span style={{ color: colors.green }}>"Authenticating with GitHub CLI to register runners..."</span><br/>
<br/>
<span style={{ color: colors.accentDim }}>gh</span> auth login --web -h github.com<br/>
TOKEN=$(gh api -X POST /orgs/<span style={{ color: colors.accentDim }}>D-sorganization</span>/actions/runners/registration-token | jq .token -r)<br/>
<br/>
<span style={{ color: colors.accentDim }}>echo</span> <span style={{ color: colors.green }}>"✅ Token acquired: $TOKEN. Starting installation..."</span><br/>
<span style={{ color: colors.textDim }}># (Execute existing fleet provisioning wrapper)</span><br/>
curl -sSL https://raw.githubusercontent.com/D-sorganization/Repository_Management/main/scripts/install-runner-wsl.sh | bash -s -- --count $OPT_RUNNERS --token $TOKEN<br/>
              </pre>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
