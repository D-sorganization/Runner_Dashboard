import { useCallback, useEffect, useState } from "react";

const PUSH_TOPICS = [
  { id: "agent.completed", label: "Agent completed" },
  { id: "agent.failed", label: "Agent failed" },
  { id: "ci.failed", label: "CI failed" },
  { id: "runner.offline", label: "Runner offline" },
  { id: "queue.stale", label: "Queue stale" },
] as const;

export function PushSettings() {
  const [publicKey, setPublicKey] = useState<string | null>(null);
  const [subscribed, setSubscribed] = useState(false);
  const [topics, setTopics] = useState<Record<string, boolean>>({});
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/push/vapid-public-key")
      .then((r) => r.json())
      .then((data) => setPublicKey(data.publicKey))
      .catch(() => setError("Failed to load VAPID key"));
  }, []);

  const subscribe = useCallback(async () => {
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
    } catch (e: any) {
      setError(e.message || "Subscription failed");
    }
  }, [publicKey, topics]);

  const unsubscribe = useCallback(async () => {
    try {
      const reg = await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.getSubscription();
      if (sub) await sub.unsubscribe();
      setSubscribed(false);
      setTopics({});
      setError(null);
    } catch (e: any) {
      setError(e.message || "Unsubscribe failed");
    }
  }, []);

  const toggleTopic = (topic: string) => {
    setTopics((prev) => ({ ...prev, [topic]: !prev[topic] }));
  };

  return (
    <div className="glass-card" style={{ padding: "16px", margin: "16px" }}>
      <h2 style={{ fontSize: "16px", marginBottom: "12px" }}>Push Notifications</h2>
      {error && (
        <div style={{ color: "var(--accent-red)", fontSize: "12px", marginBottom: "8px" }}>
          {error}
        </div>
      )}
      <div style={{ marginBottom: "12px" }}>
        {PUSH_TOPICS.map((t) => (
          <label
            key={t.id}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "8px",
              marginBottom: "8px",
              fontSize: "14px",
            }}
          >
            <input
              checked={!!topics[t.id]}
              disabled={subscribed}
              onChange={() => toggleTopic(t.id)}
              type="checkbox"
            />
            {t.label}
          </label>
        ))}
      </div>
      {subscribed ? (
        <button className="touch-button touch-button-danger" onClick={unsubscribe} type="button">
          Unsubscribe
        </button>
      ) : (
        <button className="touch-button touch-button-primary" disabled={!publicKey} onClick={subscribe} type="button">
          Subscribe
        </button>
      )}
    </div>
  );
}

function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const rawData = window.atob(base64);
  const outputArray = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; i++) {
    outputArray[i] = rawData.charCodeAt(i);
  }
  return outputArray;
}
