import { useCallback, useEffect, useState } from "react";
import { EmptyState } from "../primitives/EmptyState";
import { TouchButton } from "../primitives/TouchButton";

const PUSH_TOPICS = [
  { id: "agent.completed", label: "Agent completed" },
  { id: "agent.failed", label: "Agent failed" },
  { id: "ci.failed", label: "CI failed" },
  { id: "runner.offline", label: "Runner offline" },
  { id: "queue.stale", label: "Queue stale" },
] as const;

export default function PushSettings() {
  const [publicKey, setPublicKey] = useState<string | null>(null);
  const [subscribed, setSubscribed] = useState(false);
  const [topics, setTopics] = useState<Record<string, boolean>>({});
  const [error, setError] = useState<string | null>(null);
  const [notConfigured, setNotConfigured] = useState(false);

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
    } catch (e) {
      setError((e instanceof Error ? e.message : String(e)) || "Unsubscribe failed");
    }
  }, []);

  const toggleTopic = (topic: string) => {
    setTopics((prev) => ({ ...prev, [topic]: !prev[topic] }));
  };

  if (notConfigured) {
    return (
      <div className="glass-card push-settings">
        <h2 className="push-settings__title">Push Notifications</h2>
        <EmptyState
          title="Push notifications not configured by operator"
          description="Configure VAPID credentials before enabling browser subscriptions."
        />
      </div>
    );
  }

  return (
    <div className="glass-card push-settings">
      <h2 className="push-settings__title">Push Notifications</h2>
      {error && (
        <EmptyState
          variant="error"
          title="Push notification setup failed"
          description={error}
        />
      )}
      <div className="push-settings__topics">
        {PUSH_TOPICS.map((t) => (
          <label
            key={t.id}
            className="push-settings__topic"
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
        <TouchButton onClick={unsubscribe} variant="danger">
          Unsubscribe
        </TouchButton>
      ) : (
        <TouchButton disabled={!publicKey} onClick={subscribe} variant="primary">
          Subscribe
        </TouchButton>
      )}
    </div>
  );
}

// Returns a Uint8Array explicitly backed by a plain ArrayBuffer so the result
// is assignable to BufferSource (PushManager.subscribe.applicationServerKey).
// Since TS 5.7 a bare `Uint8Array` is generic over ArrayBufferLike and no longer
// satisfies BufferSource without this annotation.
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
