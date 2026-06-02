/**
 * AssistantSidebar.tsx — the in-app chat assistant sidebar (and the small
 * floating "Dashboard Help" button), extracted (behaviour-wise 1:1) from the
 * legacy `App.tsx` monolith as part of the decomposition epic (#836, pass 9).
 *
 * Contents:
 *  - `DashboardHelp` — the floating "?" help button (bottom-right).
 *  - `AssistantSidebar` — the resizable chat sidebar: transcript, settings
 *    panel (position/open-by-default/include-context/save-history), voice input
 *    and a `POST /api/assistant/chat` send loop.
 *
 * The render is preserved verbatim using the legacy `h = React.createElement`
 * factory (allowed during migration). localStorage access is delegated to the
 * shared `lib/assistantStorage` helpers, which the legacy App shell also
 * consumes for the open/position keys.
 */
import React from "react";
import { legacyFetch } from "../lib/api";
import { prefersReducedMotion } from "../design";
import { VoiceInputButton } from "../components/VoiceInputButton";
import { renderMarkdown } from "./assistantMarkdown";
import {
  ASST_LS,
  type AssistantMessage,
  clearAssistantTranscriptHistory,
  lsGet,
  lsLoadTranscript,
  lsSet,
} from "../lib/assistantStorage";

/* eslint-disable @typescript-eslint/no-explicit-any */
const h = React.createElement as any;

// ════════════════════════ DASHBOARD HELP ════════════════════════

export interface DashboardHelpProps {
  currentTab?: string;
}

export function DashboardHelp(p: DashboardHelpProps) {
  const currentTab = p.currentTab || "";
  const open = React.useState(false);
  const isOpen = open[0],
    setIsOpen = open[1];
  return h(
    "div",
    { style: { position: "fixed", bottom: 20, right: 20, zIndex: 500 } },
    !isOpen
      ? h(
          "button",
          {
            onClick: function () {
              setIsOpen(true);
            },
            title: "Dashboard help",
            style: {
              width: 48,
              height: 48,
              borderRadius: "50%",
              background: "var(--accent-purple, #886ce4)",
              color: "var(--text-on-accent)",
              border: "none",
              cursor: "pointer",
              fontSize: 20,
              boxShadow: "0 2px 12px rgba(0,0,0,0.3)",
            },
          },
          "?",
        )
      : h(
          "div",
          {
            style: {
              width: 320,
              background: "var(--bg-primary)",
              border: "1px solid var(--border)",
              borderRadius: 12,
              boxShadow: "0 4px 24px rgba(0,0,0,0.4)",
              padding: 16,
            },
          },
          h("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 } },
            h("strong", null, "Dashboard Help"),
            h("button", { onClick: function () { setIsOpen(false); }, className: "btn", style: { padding: "2px 8px" }, "aria-label": "Close assessment dialog" }, "Close"),
          ),
          h("div", { style: { fontSize: 12, color: "var(--text-secondary)" } }, "Current tab: " + currentTab),
        ),
  );
}

// ════════════════════════ ASSISTANT SIDEBAR ════════════════════════
export interface AssistantSidebarProps {
  currentTab?: string;
  open?: boolean;
  onToggle?: () => void;
}

export function AssistantSidebar(props: AssistantSidebarProps) {
  const currentTab = props.currentTab || "";
  const open = props.open;
  const toggle = props.onToggle;

  const ps2 = React.useState(lsGet(ASST_LS.position, "right"));
  const position = ps2[0], setPosition = ps2[1];

  const ws2 = React.useState(lsGet(ASST_LS.width, 360));
  const width = ws2[0], setWidth = ws2[1];

  const sh2 = React.useState(lsGet(ASST_LS.saveHistory, false));
  const saveHistory = sh2[0], setSaveHistory = sh2[1];

  const ts2 = React.useState<AssistantMessage[]>(function () { return saveHistory ? lsLoadTranscript() : []; });
  const transcript = ts2[0], setTranscript = ts2[1];

  const ic2 = React.useState(lsGet(ASST_LS.includeContext, true));
  const includeCtx = ic2[0], setIncludeCtx = ic2[1];

  const obds = React.useState(lsGet(ASST_LS.openByDefault, false));
  const openByDefault = obds[0], setOpenByDefault = obds[1];

  const inputS = React.useState("");
  const inputVal = inputS[0], setInputVal = inputS[1];

  const loadS = React.useState(false);
  const loading = loadS[0], setLoading = loadS[1];

  const showSettingsS = React.useState(false);
  const showSettings = showSettingsS[0], setShowSettings = showSettingsS[1];

  const transcriptRef = React.useRef<any>(null);
  const dragStartX = React.useRef<any>(null);
  const dragStartW = React.useRef<any>(null);

  // Scroll to bottom when transcript changes
  React.useEffect(function () {
    if (transcriptRef.current) {
      transcriptRef.current.scrollTop = transcriptRef.current.scrollHeight;
    }
  }, [transcript, open]);

  // Persist state changes
  React.useEffect(function () { lsSet(ASST_LS.position, position); }, [position]);
  React.useEffect(function () { lsSet(ASST_LS.width, width); }, [width]);
  React.useEffect(function () { lsSet(ASST_LS.includeContext, includeCtx); }, [includeCtx]);
  React.useEffect(function () { lsSet(ASST_LS.openByDefault, openByDefault); }, [openByDefault]);
  React.useEffect(function () { lsSet(ASST_LS.saveHistory, saveHistory); }, [saveHistory]);
  React.useEffect(function () {
    if (!saveHistory) {
      clearAssistantTranscriptHistory();
      return;
    }
    const capped = transcript.length > 200 ? transcript.slice(-200) : transcript;
    lsSet(ASST_LS.transcript, capped);
    try { localStorage.setItem(ASST_LS.transcriptTimestamp, String(Date.now())); } catch { /* ignore quota errors */ }
  }, [transcript, saveHistory]);

  function getPageContext() {
    return {
      tab: currentTab,
      url: window.location.href,
      selection: window.getSelection ? (window.getSelection()?.toString() || "").slice(0, 500) : "",
    };
  }

  function sendMessage() {
    const msg = inputVal.trim();
    if (!msg || loading) return;
    setInputVal("");
    const userMsg = { role: "user", content: msg, id: Date.now() } as AssistantMessage;
    setTranscript(function (t) { return t.concat([userMsg]); });
    setLoading(true);

    // Build request with context
    const body: any = {
      prompt: msg,
      context: {
        current_tab: currentTab,
        selected_run_id: null,
        selected_items: [],
      },
    };
    if (includeCtx) {
      const ctx = getPageContext();
      body.context.dashboard_state = ctx;
    }

    legacyFetch("/api/assistant/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (data) {
        const reply = data.response || data.message || JSON.stringify(data);
        const asstMsg = { role: "assistant", content: reply, id: Date.now() + 1 } as AssistantMessage;
        setTranscript(function (t) { return t.concat([asstMsg]); });
      })
      .catch(function (err) {
        const errMsg = { role: "assistant", content: "Error: " + (err.message || "request failed"), id: Date.now() + 1 } as AssistantMessage;
        setTranscript(function (t) { return t.concat([errMsg]); });
      })
      .finally(function () { setLoading(false); });
  }

  function handleTranscription(text: string) {
    setInputVal(function (prev) {
      return prev ? prev + " " + text : text;
    });
  }

  function onKeyDown(e: any) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  }

  function startDrag(e: any) {
    dragStartX.current = e.clientX;
    dragStartW.current = width;
    e.preventDefault();
    function onMove(ev: any) {
      const delta = position === "right" ? dragStartX.current - ev.clientX : ev.clientX - dragStartX.current;
      const newW = Math.min(600, Math.max(280, dragStartW.current + delta));
      setWidth(newW);
    }
    function onUp() {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    }
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  }

  // Sidebar container
  const sidebarStyle: any = {
    width: open ? width : 0,
    minWidth: open ? width : 0,
    maxWidth: open ? width : 0,
    overflow: "hidden",
    transition: prefersReducedMotion() ? "none" : "width 0.2s, min-width 0.2s, max-width 0.2s",
    flexShrink: 0,
    position: "relative",
    background: "var(--bg-secondary)",
    borderLeft: position === "right" ? "1px solid var(--border)" : "none",
    borderRight: position === "left" ? "1px solid var(--border)" : "none",
    display: "flex",
    flexDirection: "column",
    height: "calc(100vh - 56px)",
    top: 0,
  };

  const dragHandleStyle: any = {
    position: "absolute",
    top: 0,
    bottom: 0,
    width: 5,
    cursor: "col-resize",
    background: "transparent",
    zIndex: 10,
    left: position === "right" ? 0 : "auto",
    right: position === "left" ? 0 : "auto",
  };

  const headerStyle: any = {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "10px 12px",
    borderBottom: "1px solid var(--border)",
    flexShrink: 0,
    background: "var(--bg-tertiary)",
  };

  const transcriptStyle: any = {
    flex: 1,
    overflowY: "auto",
    padding: "12px",
    display: "flex",
    flexDirection: "column",
    gap: 8,
  };

  const inputAreaStyle: any = {
    borderTop: "1px solid var(--border)",
    padding: "8px",
    flexShrink: 0,
    display: "flex",
    flexDirection: "column",
    gap: 6,
  };

  const settingsPanel = showSettings
    ? h("div", { style: { padding: "12px", display: "flex", flexDirection: "column", gap: 12, overflowY: "auto", flex: 1 } },
        h("div", { style: { display: "flex", alignItems: "center", gap: 8 } },
          h("button", { onClick: function () { setShowSettings(false); }, style: { background: "none", border: "none", color: "var(--accent-blue)", cursor: "pointer", fontSize: 13 }, "aria-label": "Back to settings" }, "← Back"),
          h("span", { style: { fontWeight: 600, fontSize: 13 } }, "Settings"),
        ),
        h("label", { style: { fontSize: 12, display: "flex", flexDirection: "column", gap: 4 } },
          "Position",
          h("div", { style: { display: "flex", gap: 12, marginTop: 4 } },
            h("label", { style: { display: "flex", alignItems: "center", gap: 4, cursor: "pointer", fontSize: 12 } },
              h("input", { type: "radio", name: "asst-pos", checked: position === "right", onChange: function () { setPosition("right"); }, style: { accentColor: "var(--accent-blue)" } }),
              "Right"
            ),
            h("label", { style: { display: "flex", alignItems: "center", gap: 4, cursor: "pointer", fontSize: 12 } },
              h("input", { type: "radio", name: "asst-pos", checked: position === "left", onChange: function () { setPosition("left"); }, style: { accentColor: "var(--accent-blue)" } }),
              "Left"
            ),
          ),
        ),
        h("label", { style: { fontSize: 12, display: "flex", alignItems: "center", gap: 8, cursor: "pointer" } },
          h("input", { type: "checkbox", checked: openByDefault, onChange: function (e: any) { setOpenByDefault(e.target.checked); }, style: { accentColor: "var(--accent-blue)" } }),
          "Open by default"
        ),
        h("label", { style: { fontSize: 12, display: "flex", alignItems: "center", gap: 8, cursor: "pointer" } },
          h("input", { type: "checkbox", checked: includeCtx, onChange: function (e: any) { setIncludeCtx(e.target.checked); }, style: { accentColor: "var(--accent-blue)" } }),
          "Include page context with messages"
        ),
        h("label", { style: { fontSize: 12, display: "flex", alignItems: "center", gap: 8, cursor: "pointer" } },
          h("input", {
            type: "checkbox",
            checked: saveHistory,
            onChange: function (e: any) {
              const next = e.target.checked;
              setSaveHistory(next);
              if (!next) {
                setTranscript([]);
                clearAssistantTranscriptHistory();
              }
            },
            style: { accentColor: "var(--accent-blue)" },
          }),
          "Save chat history"
        ),
        h("button", {
          onClick: function () {
            setTranscript([]);
            clearAssistantTranscriptHistory();
            setShowSettings(false);
          },
          style: { background: "var(--accent-red)", color: "var(--text-on-accent)", border: "none", borderRadius: 6, padding: "6px 12px", cursor: "pointer", fontSize: 12, width: "100%", marginTop: 8 },
        }, "Clear chat history"),
      )
    : null;

  const chatPanel = !showSettings
    ? h(React.Fragment, null,
        h("div", { ref: transcriptRef, style: transcriptStyle },
          transcript.length === 0
            ? h("div", { style: { color: "var(--text-muted)", fontSize: 12, textAlign: "center", marginTop: 24 } }, "Ask anything about the dashboard…")
            : transcript.map(function (msg) {
                const isUser = msg.role === "user";
                const bubbleStyle: any = {
                  alignSelf: isUser ? "flex-end" : "flex-start",
                  background: isUser ? "var(--accent-blue)" : "var(--bg-tertiary)",
                  color: isUser ? "var(--text-on-accent)" : "var(--text-primary)",
                  borderRadius: isUser ? "14px 14px 4px 14px" : "14px 14px 14px 4px",
                  padding: "8px 12px",
                  maxWidth: "92%",
                  fontSize: 13,
                  lineHeight: 1.5,
                  wordBreak: "break-word",
                };
                return h("div", { key: msg.id, style: bubbleStyle },
                  isUser ? msg.content : renderMarkdown(msg.content)
                );
              }),
          loading ? h("div", { style: { alignSelf: "flex-start", color: "var(--text-muted)", fontSize: 12, fontStyle: "italic" } }, "Thinking…") : null,
        ),
        h("div", { style: inputAreaStyle },
          h("textarea", {
            value: inputVal,
            onChange: function (e: any) { setInputVal(e.target.value); },
            onKeyDown: onKeyDown,
            placeholder: "Ask a question… (Enter to send)",
            rows: 3,
            style: {
              width: "100%",
              background: "var(--bg-tertiary)",
              border: "1px solid var(--border)",
              borderRadius: 8,
              color: "var(--text-primary)",
              padding: "8px",
              fontSize: 13,
              resize: "none",
              fontFamily: "inherit",
              outline: "none",
            },
          }),
          h("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center" } },
            h("span", { style: { fontSize: 11, color: "var(--text-muted)" } }, "Shift+Enter for newline"),
            h("div", { style: { display: "flex", gap: 8, alignItems: "center" } },
              h(VoiceInputButton, { onTranscription: handleTranscription, disabled: loading }),
              h("button", {
                onClick: sendMessage,
                disabled: loading || !inputVal.trim(),
                style: {
                  background: "var(--accent-blue)",
                  color: "var(--text-on-accent)",
                  border: "none",
                  borderRadius: 6,
                  padding: "5px 14px",
                  cursor: loading || !inputVal.trim() ? "default" : "pointer",
                  fontSize: 13,
                  opacity: loading || !inputVal.trim() ? 0.5 : 1,
                },
              }, "Send"),
            ),
          ),
        ),
      )
    : null;

  return h("div", {
    style: sidebarStyle,
    // a11y (#833): a bare <div> may not carry aria-label (aria-prohibited-attr).
    // Promote the chat panel to a complementary landmark so the label is valid
    // and the region is reachable via the screen-reader landmark rotor.
    role: "complementary",
    "aria-label": "Chat sidebar",
    "aria-hidden": open ? undefined : "true",
  },
    open ? h("div", { style: dragHandleStyle, onMouseDown: startDrag }) : null,
    open ? h(React.Fragment, null,
      h("div", { style: headerStyle },
        h("span", { style: { fontWeight: 600, fontSize: 13 } }, "💬 Chat"),
        h("div", { style: { display: "flex", gap: 6 } },
          h("label", {
            title: "Save chat history",
            style: { display: "flex", alignItems: "center", gap: 4, color: "var(--text-muted)", cursor: "pointer", fontSize: 11 },
          },
            h("input", {
              type: "checkbox",
              checked: saveHistory,
              "aria-label": "Save chat history",
              onChange: function (e: any) {
                const next = e.target.checked;
                setSaveHistory(next);
                if (!next) {
                  setTranscript([]);
                  clearAssistantTranscriptHistory();
                }
              },
              style: { accentColor: "var(--accent-blue)" },
            }),
            "History"
          ),
          h("button", {
            onClick: function () { setShowSettings(function (s) { return !s; }); },
            title: "Settings",
            "aria-label": "Assistant settings",
            "aria-expanded": showSettings ? "true" : "false",
            style: { background: "none", border: "none", color: showSettings ? "var(--accent-blue)" : "var(--text-muted)", cursor: "pointer", fontSize: 15, lineHeight: 1 },
          }, "⚙️"),
          h("button", {
            onClick: toggle,
            title: "Close",
            "aria-label": "Close assistant",
            style: { background: "none", border: "none", color: "var(--text-muted)", cursor: "pointer", fontSize: 16, lineHeight: 1 },
          }, "×"),
        ),
      ),
      settingsPanel,
      chatPanel,
    ) : null,
  );
}
