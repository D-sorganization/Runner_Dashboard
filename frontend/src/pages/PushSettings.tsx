import React, { useCallback, useEffect, useState } from "react";
import { EmptyState } from "../primitives/EmptyState";
import { TouchButton } from "../primitives/TouchButton";

const PUSH_TOPICS = [
  { id: "agent.completed", label: "Agent completed", desc: "Notify when background agent completes a run" },
  { id: "agent.failed", label: "Agent failed", desc: "Notify when an agent encounters an error or failure" },
  { id: "ci.failed", label: "CI failed", desc: "Notify on CI/CD workflow test failures" },
  { id: "runner.offline", label: "Runner offline", desc: "Notify when a self-hosted runner drops offline" },
  { id: "queue.stale", label: "Queue stale", desc: "Notify when jobs exceed queued thresholds" },
  { id: "staff.escalation", label: "Staff escalation", desc: "Notify on proposals and approvals waiting on you" },
] as const;

export default function PushSettings() {
  const [publicKey, setPublicKey] = useState<string | null>(null);
  const [subscribed, setSubscribed] = useState(false);
  const [topics, setTopics] = useState<Record<string, boolean>>({});
  const [error, setError] = useState<string | null>(null);
  const [notConfigured, setNotConfigured] = useState(false);
  const [testStatus, setTestStatus] = useState<"idle" | "sending" | "success" | "error">("idle");
  const [testMessage, setTestMessage] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/push/vapid-public-key")
      .then((r) => {
        if (r.status === 503) {
          setNotConfigured(true);
          return null;
        }
        if (!r.ok) throw new Error(`Failed to load VAPID key: ${r.status}`);
        return r.json();
      })
      .then((data) => {
        if (data && data.publicKey) setPublicKey(data.publicKey);
      })
      .catch(() => setError("Failed to load VAPID key"));
  }, []);

  const subscribe = useCallback(async () => {
    if (notConfigured) {
      return;
    }
    if (!publicKey || !("serviceWorker" in navigator) || !("PushManager" in window)) {
      setError("Push notifications are not supported in this browser.");
      return;
    }
    try {
      const reg = await navigator.serviceWorker.ready;
      const subscription = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(publicKey),
      });
      const payload = await subscription.toJSON();
      const selectedTopics = Object.entries(topics)
        .filter(([, v]) => v)
        .map(([k]) => k);
      const resp = await fetch("/api/push/subscribe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          endpoint: payload.endpoint,
          keys: payload.keys,
          topics: selectedTopics.length ? selectedTopics : ["agent.completed"],
        }),
      });
      if (!resp.ok) throw new Error(`Subscribe failed: ${resp.status}`);
      setSubscribed(true);
      setError(null);
    } catch (e) {
      setError((e instanceof Error ? e.message : String(e)) || "Subscription failed");
    }
  }, [notConfigured, publicKey, topics]);

  const unsubscribe = useCallback(async () => {
    try {
      const reg = await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.getSubscription();
      if (sub) await sub.unsubscribe();
      setSubscribed(false);
      setTopics({});
      setError(null);
      setTestStatus("idle");
      setTestMessage(null);
    } catch (e) {
      setError((e instanceof Error ? e.message : String(e)) || "Unsubscribe failed");
    }
  }, []);

  const sendTestNotification = useCallback(async () => {
    setTestStatus("sending");
    setTestMessage(null);
    try {
      const resp = await fetch("/api/push/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          topic: "agent.completed",
          deep_link: "/settings/push",
        }),
      });
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${resp.status}`);
      }
      const data = await resp.json();
      setTestStatus("success");
      setTestMessage(
        data.sent > 0
          ? "Test notification dispatched to your device."
          : "Dispatched, but no matching active subscriptions found."
      );
    } catch (e) {
      setTestStatus("error");
      setTestMessage((e instanceof Error ? e.message : String(e)) || "Failed to send test push");
    }
  }, []);

  const toggleTopic = (topic: string) => {
    setTopics((prev) => ({ ...prev, [topic]: !prev[topic] }));
  };

  if (notConfigured) {
    return (
      <div
        className="push-settings"
        style={{
          backgroundColor: "var(--bg-secondary, #161b22)",
          border: "1px solid var(--border, #30363d)",
          borderRadius: "var(--radius-md, 8px)",
          padding: "20px",
        }}
      >
        <h2 className="push-settings__title" style={{ color: "var(--text-primary, #ffffff)" }}>
          Push Notifications
        </h2>
        <EmptyState
          title="Push notifications not configured by operator"
          description="Configure VAPID credentials before enabling browser subscriptions. Refer to docs/runbooks/phone-access-tailnet.md for setup instructions."
        />
        <div
          style={{
            fontSize: "13px",
            lineHeight: "1.5",
            color: "var(--text-secondary, #8b949e)",
            backgroundColor: "var(--bg-tertiary, #21262d)",
            border: "1px solid var(--border, #30363d)",
            borderRadius: "6px",
            padding: "12px 14px",
            marginTop: "12px",
          }}
        >
          Run <code>python -m push keygen</code> on the hub host to generate keys, add them to your environment, and restart the service.
        </div>
      </div>
    );
  }

  return (
    <div
      className="push-settings"
      style={{
        backgroundColor: "var(--bg-secondary, #161b22)",
        border: "1px solid var(--border, #30363d)",
        borderRadius: "var(--radius-md, 8px)",
        padding: "20px",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "4px" }}>
        <h2 className="push-settings__title" style={{ color: "var(--text-primary, #ffffff)" }}>
          Push Notifications
        </h2>
        <span
          style={{
            fontSize: "12px",
            fontWeight: 500,
            padding: "2px 8px",
            borderRadius: "12px",
            backgroundColor: subscribed ? "rgba(35, 134, 54, 0.2)" : "var(--bg-tertiary, #21262d)",
            color: subscribed ? "var(--status-success, #3fb950)" : "var(--text-muted, #8b949e)",
            border: `1px solid ${subscribed ? "var(--status-success, #3fb950)" : "var(--border, #30363d)"}`,
          }}
        >
          {subscribed ? "Active" : "Not subscribed"}
        </span>
      </div>

      <div
        style={{
          fontSize: "13px",
          lineHeight: "1.5",
          color: "var(--text-secondary, #8b949e)",
          backgroundColor: "var(--bg-tertiary, #21262d)",
          border: "1px solid var(--border, #30363d)",
          borderRadius: "6px",
          padding: "10px 12px",
          marginBottom: "12px",
        }}
      >
        <strong>iPhone setup:</strong> On iPhone: Share → Add to Home Screen, then open from the icon to enable notifications (iOS 16.4+).
      </div>

      {error && (
        <EmptyState
          variant="error"
          title="Push notification setup failed"
          description={error}
        />
      )}

      <div className="push-settings__topics" style={{ margin: "8px 0 16px 0" }}>
        {PUSH_TOPICS.map((t) => (
          <label
            key={t.id}
            className="push-settings__topic"
            style={{
              cursor: subscribed ? "default" : "pointer",
              padding: "6px 8px",
              borderRadius: "6px",
              display: "flex",
              alignItems: "center",
              gap: "10px",
            }}
          >
            <input
              checked={!!topics[t.id]}
              disabled={subscribed}
              onChange={() => toggleTopic(t.id)}
              type="checkbox"
              style={{
                width: "16px",
                height: "16px",
                accentColor: "var(--accent-blue, #58a6ff)",
                cursor: subscribed ? "default" : "pointer",
              }}
            />
            <div style={{ display: "flex", flexDirection: "column" }}>
              <span style={{ fontSize: "14px", fontWeight: 500, color: "var(--text-primary, #ffffff)" }}>
                {t.label}
              </span>
              {"desc" in t && (
                <span style={{ fontSize: "12px", color: "var(--text-muted, #8b949e)" }}>
                  {t.desc}
                </span>
              )}
            </div>
          </label>
        ))}
      </div>

      <div style={{ display: "flex", gap: "10px", alignItems: "center", flexWrap: "wrap" }}>
        {subscribed ? (
          <>
            <TouchButton onClick={unsubscribe} variant="danger">
              Unsubscribe
            </TouchButton>
            <TouchButton
              disabled={testStatus === "sending"}
              onClick={sendTestNotification}
              variant="default"
            >
              {testStatus === "sending" ? "Sending…" : "Send test notification"}
            </TouchButton>
          </>
        ) : (
          <TouchButton disabled={!publicKey} onClick={subscribe} variant="primary">
            Subscribe
          </TouchButton>
        )}
      </div>

      {testMessage && (
        <div
          style={{
            marginTop: "12px",
            padding: "8px 12px",
            borderRadius: "6px",
            fontSize: "13px",
            backgroundColor: testStatus === "error" ? "rgba(248, 81, 73, 0.15)" : "rgba(56, 139, 253, 0.15)",
            color: testStatus === "error" ? "var(--status-danger, #f85149)" : "var(--accent-blue, #58a6ff)",
            border: `1px solid ${testStatus === "error" ? "var(--status-danger, #da3633)" : "var(--accent-blue, #388bfd)"}`,
          }}
        >
          {testMessage}
        </div>
      )}
    </div>
  );
}

function urlBase64ToUint8Array(base64String: string): Uint8Array<ArrayBuffer> {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const rawData = window.atob(base64);
  const outputArray = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; i++) {
    outputArray[i] = rawData.charCodeAt(i);
  }
  return outputArray;
}
