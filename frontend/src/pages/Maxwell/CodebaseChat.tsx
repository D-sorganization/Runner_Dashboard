/**
 * CodebaseChat — in-app codebase Q&A assistant (issue #838).
 *
 * Reuses the streaming chat experience (`ChatBubble`) but scopes a conversation
 * to a single repository: the operator picks a repo and supplies the on-disk
 * `repo_root`, and those are forwarded through `POST /api/maxwell/chat` to
 * Maxwell-Daemon, which jails its agentic codebase tools (read_file/grep_files/
 * glob_files/run_bash) to that root. Codebase-flavored quick-chips seed common
 * questions ("where is X handled?", "what does /api/queue do?").
 *
 * The daemon-side capability is tracked separately (Maxwell_Daemon#948). If the
 * connected daemon does not support codebase Q&A it answers 501, which the proxy
 * degrades into a readable message — this component simply renders whatever text
 * the stream returns, so it never dead-ends.
 *
 * Self-contained by design: it owns its own state and reaches into no shell or
 * page internals, so it can be slotted into the Help/About surface (#822)
 * without touching the shell. `fetchImpl` is injectable so tests drive it
 * without the network (LoD).
 */
import React, { useCallback, useEffect, useState } from "react";
import { ChatBubble } from "./ChatBubble";
import type { ChatMessage } from "./mobileTypes";
import { CODEBASE_QUICK_CHIPS } from "./mobileTypes";

export interface CodebaseChatProps {
  /** Injectable fetch (tests). Defaults to the global `fetch`. */
  fetchImpl?: typeof fetch;
}

/** Minimal shape of a `/api/repos` entry we care about (extra fields tolerated). */
interface RepoEntry {
  name: string;
}

export function CodebaseChat({ fetchImpl }: CodebaseChatProps): React.ReactElement {
  const doFetch = fetchImpl ?? (typeof fetch !== "undefined" ? fetch : undefined);

  const [repos, setRepos] = useState<string[]>([]);
  const [repo, setRepo] = useState("");
  const [repoRoot, setRepoRoot] = useState("");

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);

  // Lazily fetch the repo list for the picker; tolerate failure (manual entry).
  useEffect(() => {
    if (!doFetch) return;
    let cancelled = false;
    doFetch("/api/repos", { headers: { Accept: "application/json" } })
      .then((r) => (r.ok ? r.json() : []))
      .then((data: RepoEntry[] | { repos?: RepoEntry[] }) => {
        if (cancelled) return;
        const list = Array.isArray(data) ? data : (data.repos ?? []);
        const names = list
          .map((it) => it?.name)
          .filter((n): n is string => typeof n === "string" && n.length > 0);
        setRepos(names);
      })
      .catch(() => {
        /* picker degrades to manual repo entry */
      });
    return () => {
      cancelled = true;
    };
  }, [doFetch]);

  const updateMessage = useCallback((id: number, patch: Partial<ChatMessage>) => {
    setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, ...patch } : m)));
  }, []);

  const send = useCallback(
    async (text?: string) => {
      const msg = (text ?? input).trim();
      if (!msg || sending || !doFetch) return;
      setInput("");

      const now = Date.now();
      const userMsg: ChatMessage = { id: now, role: "operator", content: msg };
      const assistantId = now + 1;
      setMessages((prev) => [
        ...prev,
        userMsg,
        { id: assistantId, role: "maxwell", content: "", streaming: true },
      ]);
      setSending(true);

      try {
        const resp = await doFetch("/api/maxwell/chat", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
          },
          body: JSON.stringify({
            message: msg,
            history: messages.slice(-12),
            ...(repo ? { repo } : {}),
            ...(repoRoot ? { repo_root: repoRoot } : {}),
          }),
        });

        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

        if (resp.body && typeof window !== "undefined" && window.TextDecoder) {
          const reader = resp.body.getReader();
          const decoder = new TextDecoder();
          let acc = "";
          // eslint-disable-next-line no-constant-condition
          while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            acc += decoder.decode(value, { stream: true });
            updateMessage(assistantId, { content: acc || "Receiving…", streaming: true });
          }
          updateMessage(assistantId, {
            content: acc || "The assistant returned an empty response.",
            streaming: false,
          });
        } else {
          const data = await resp.json().catch(() => ({}));
          updateMessage(assistantId, {
            content:
              data.response ?? data.message ?? "The assistant returned an empty response.",
            streaming: false,
          });
        }
      } catch {
        updateMessage(assistantId, {
          content:
            "Could not reach the codebase assistant. Maxwell-Daemon must be running — " +
            "start it from Local Tools, then retry.",
          streaming: false,
          error: true,
        });
      } finally {
        setSending(false);
      }
    },
    [input, sending, doFetch, messages, repo, repoRoot, updateMessage],
  );

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send();
    }
  }

  const ready = Boolean(repo || repoRoot);

  return (
    <section
      aria-label="Codebase assistant"
      style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 16 }}
    >
      <h3 style={headingStyle}>Codebase assistant</h3>
      <p style={{ margin: 0, fontSize: 12, color: "var(--text-muted)" }}>
        Ask questions about a repository. Pick a repo and point the assistant at its
        local checkout; Maxwell-Daemon reads the code to answer.
      </p>

      {/* Repo picker */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
        <label style={{ display: "flex", flexDirection: "column", gap: 2, fontSize: 11 }}>
          <span style={{ color: "var(--text-muted)" }}>Repository</span>
          <input
            aria-label="Repository"
            list="codebase-chat-repos"
            value={repo}
            onChange={(e) => setRepo(e.target.value)}
            placeholder="Runner_Dashboard"
            style={inputStyle}
          />
          <datalist id="codebase-chat-repos">
            {repos.map((r) => (
              <option key={r} value={r} />
            ))}
          </datalist>
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 2, fontSize: 11, flex: 1 }}>
          <span style={{ color: "var(--text-muted)" }}>Local path (repo_root)</span>
          <input
            aria-label="Local repository path"
            value={repoRoot}
            onChange={(e) => setRepoRoot(e.target.value)}
            placeholder="/home/runner/Runner_Dashboard"
            style={inputStyle}
          />
        </label>
      </div>

      {/* Message history */}
      <div
        aria-label="Codebase chat history"
        aria-live="polite"
        style={{
          border: "1px solid var(--border)",
          borderRadius: 10,
          display: "flex",
          flexDirection: "column",
          gap: 8,
          minHeight: 120,
          maxHeight: 240,
          overflowY: "auto",
          padding: 12,
        }}
      >
        {messages.length === 0 ? (
          <div style={{ color: "var(--text-muted)", fontSize: 12, margin: "auto", textAlign: "center" }}>
            {ready
              ? "Ask where something is handled, what an endpoint does, or how a subsystem works."
              : "Pick a repository above to start a codebase Q&A session."}
          </div>
        ) : (
          messages.map((m) => <ChatBubble key={m.id} message={m} />)
        )}
      </div>

      {/* Codebase quick-chips */}
      <div aria-label="Codebase quick questions" style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
        {CODEBASE_QUICK_CHIPS.map((chip) => (
          <button
            key={chip}
            type="button"
            aria-label={`Ask: ${chip}`}
            disabled={sending || !ready}
            onClick={() => void send(chip)}
            style={chipStyle}
          >
            {chip}
          </button>
        ))}
      </div>

      {/* Composer */}
      <div style={{ display: "flex", gap: 8 }}>
        <textarea
          aria-label="Ask the codebase assistant"
          disabled={sending || !ready}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          rows={1}
          placeholder={ready ? "Ask about this repository…" : "Pick a repository first"}
          style={{
            background: "var(--bg-tertiary)",
            border: "1px solid var(--border)",
            borderRadius: 8,
            boxSizing: "border-box",
            color: "var(--text-primary)",
            flex: 1,
            fontFamily: "inherit",
            fontSize: 13,
            minHeight: 40,
            padding: "10px 12px",
            resize: "none",
          }}
        />
        <button
          type="button"
          aria-label="Send question to the codebase assistant"
          disabled={sending || !input.trim() || !ready}
          onClick={() => void send()}
          style={{ ...chipStyle, minWidth: 64, fontWeight: 600 }}
        >
          {sending ? "…" : "Send"}
        </button>
      </div>
    </section>
  );
}

const headingStyle: React.CSSProperties = {
  margin: "0 0 2px 0",
  fontSize: 12,
  fontWeight: 600,
  textTransform: "uppercase",
  letterSpacing: "0.04em",
  color: "var(--text-muted)",
};

const inputStyle: React.CSSProperties = {
  background: "var(--bg-tertiary)",
  border: "1px solid var(--border)",
  borderRadius: 6,
  color: "var(--text-primary)",
  fontSize: 12,
  padding: "6px 8px",
};

const chipStyle: React.CSSProperties = {
  padding: "4px 10px",
  borderRadius: 6,
  border: "1px solid var(--border)",
  background: "var(--bg-primary)",
  color: "var(--accent-blue)",
  fontSize: 12,
  cursor: "pointer",
};
