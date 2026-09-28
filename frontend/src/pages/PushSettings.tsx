import { useCallback, useEffect, useState } from "react";
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
      <div className="push-settings">
        <h2 className="push-settings__title">Push Notifications</h2>
        <EmptyState
          title="Push notifications not configured by operator"
          description="Configure VAPID credentials before enabling browser subscriptions. Refer to docs/runbooks/phone-access-tailnet.md for setup instructions."
        />
        <div className="push-settings__note">
          Run <code>python -m push keygen</code> on the hub host to generate keys, add them to your
          environment, and restart the service.
        </div>
      </div>
    );
  }

  return (
    <div className="push-settings">
      <div className="push-settings__header">
        <h2 className="push-settings__title">Push Notifications</h2>
        <span className={`push-settings__status${subscribed ? " push-settings__status--active" : ""}`}>
          {subscribed ? "Active" : "Not subscribed"}
        </span>
      </div>

      <div className="push-settings__note">
        <strong>iPhone setup:</strong> On iPhone: Share → Add to Home Screen, then open from the icon to
        enable notifications (iOS 16.4+).
      </div>

      {error && <EmptyState variant="error" title="Push notification setup failed" description={error} />}

      <div className="push-settings__topics">
        {PUSH_TOPICS.map((t) => (
          <label
            key={t.id}
            className={`push-settings__topic${subscribed ? " push-settings__topic--locked" : ""}`}
          >
            <input
              className="push-settings__checkbox"
              checked={!!topics[t.id]}
              disabled={subscribed}
              onChange={() => toggleTopic(t.id)}
              type="checkbox"
            />
            <span className="push-settings__topic-text">
              <span className="push-settings__topic-label">{t.label}</span>
              <span className="push-settings__topic-desc">{t.desc}</span>
            </span>
          </label>
        ))}
      </div>

      <div className="push-settings__actions">
        {subscribed ? (
          <>
            <TouchButton onClick={unsubscribe} variant="danger">
              Unsubscribe
            </TouchButton>
            <TouchButton disabled={testStatus === "sending"} onClick={sendTestNotification} variant="default">
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
          className={`push-settings__result${testStatus === "error" ? " push-settings__result--error" : ""}`}
          role={testStatus === "error" ? "alert" : "status"}
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
