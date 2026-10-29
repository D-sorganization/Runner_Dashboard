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

interface HydratedPushSubscription {
  id: number;
  endpoint: string;
  topics: string[];
  quiet_hours_start?: string | null;
  quiet_hours_end?: string | null;
}

export default function PushSettings() {
  const [publicKey, setPublicKey] = useState<string | null>(null);
  const [subscribed, setSubscribed] = useState(false);
  const [subscriptionId, setSubscriptionId] = useState<number | null>(null);
  const [topics, setTopics] = useState<Record<string, boolean>>({});
  const [quietHoursStart, setQuietHoursStart] = useState("");
  const [quietHoursEnd, setQuietHoursEnd] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notConfigured, setNotConfigured] = useState(false);
  const [testStatus, setTestStatus] = useState<"idle" | "sending" | "success" | "error">("idle");
  const [testMessage, setTestMessage] = useState<string | null>(null);

  useEffect(() => {
    const reqInit: RequestInit = {
      credentials: "same-origin",
      headers: { "X-Requested-With": "XMLHttpRequest" },
    };
    fetch("/api/push/vapid-public-key", reqInit)
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

    fetch("/api/push/subscriptions", reqInit)
      .then((r) => (r && r.ok ? r.json() : null))
      .then((data) => {
        const list: HydratedPushSubscription[] = Array.isArray(data?.subscriptions)
          ? data.subscriptions
          : [];
        if (list.length === 0) return;
        const active = list[0];
        setSubscribed(true);
        setSubscriptionId(typeof active.id === "number" ? active.id : 1);
        setTopics(Object.fromEntries((active.topics ?? []).map((t) => [t, true])));
        setQuietHoursStart(active.quiet_hours_start ?? "");
        setQuietHoursEnd(active.quiet_hours_end ?? "");
      })
      .catch(() => {});
  }, []);

  const persistPreferences = useCallback(
    async (nextTopicsMap: Record<string, boolean>, nextStart: string, nextEnd: string) => {
      const selectedTopics = Object.entries(nextTopicsMap)
        .filter(([, v]) => v)
        .map(([k]) => k);
      try {
        const resp = await fetch(`/api/push/subscribe/${subscriptionId ?? 1}`, {
          method: "PATCH",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
          body: JSON.stringify({
            topics: selectedTopics,
            quiet_hours_start: nextStart || null,
            quiet_hours_end: nextEnd || null,
          }),
        });
        if (!resp.ok) throw new Error(`Failed to save push settings: ${resp.status}`);
      } catch (e) {
        setError((e instanceof Error ? e.message : String(e)) || "Failed to save push settings");
      }
    },
    [subscriptionId],
  );

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
      const effectiveTopics = selectedTopics.length ? selectedTopics : ["agent.completed"];
      const resp = await fetch("/api/push/subscribe", {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-Requested-With": "XMLHttpRequest",
        },
        body: JSON.stringify({
          endpoint: payload.endpoint,
          keys: payload.keys,
          topics: effectiveTopics,
          quiet_hours_start: quietHoursStart || null,
          quiet_hours_end: quietHoursEnd || null,
        }),
      });
      if (!resp.ok) throw new Error(`Subscribe failed: ${resp.status}`);
      const data = await resp.json().catch(() => ({}));
      setSubscriptionId(typeof data?.id === "number" ? data.id : 1);
      setSubscribed(true);
      setError(null);
    } catch (e) {
      setError((e instanceof Error ? e.message : String(e)) || "Subscription failed");
    }
  }, [notConfigured, publicKey, quietHoursEnd, quietHoursStart, topics]);

  const unsubscribe = useCallback(async () => {
    try {
      if ("serviceWorker" in navigator) {
        const reg = await navigator.serviceWorker.ready;
        const sub = await reg?.pushManager?.getSubscription?.();
        if (sub) await sub.unsubscribe();
      }
      const targetId = subscriptionId ?? 1;
      const resp = await fetch(`/api/push/subscribe/${targetId}`, {
        method: "DELETE",
        credentials: "same-origin",
        headers: {
          "X-Requested-With": "XMLHttpRequest",
        },
      });
      if (!resp.ok && resp.status !== 404) {
        throw new Error(`Unsubscribe failed: ${resp.status}`);
      }
      setSubscribed(false);
      setSubscriptionId(null);
      setTopics({});
      setQuietHoursStart("");
      setQuietHoursEnd("");
      setError(null);
      setTestStatus("idle");
      setTestMessage(null);
    } catch (e) {
      setError((e instanceof Error ? e.message : String(e)) || "Unsubscribe failed");
    }
  }, [subscriptionId]);

  const sendTestNotification = useCallback(async () => {
    setTestStatus("sending");
    setTestMessage(null);
    try {
      const resp = await fetch("/api/push/test", {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-Requested-With": "XMLHttpRequest",
        },
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
    const nextTopics = { ...topics, [topic]: !topics[topic] };
    setTopics(nextTopics);
    if (subscribed) void persistPreferences(nextTopics, quietHoursStart, quietHoursEnd);
  };

  const updateQuietHours = (nextStart: string, nextEnd: string) => {
    setQuietHoursStart(nextStart);
    setQuietHoursEnd(nextEnd);
    if (subscribed) void persistPreferences(topics, nextStart, nextEnd);
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
          <label key={t.id} className="push-settings__topic">
            <input
              className="push-settings__checkbox"
              checked={!!topics[t.id]}
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

      <div className="push-settings__quiet-hours">
        <h3 className="push-settings__subtitle">Quiet hours (UTC)</h3>
        <div className="push-settings__quiet-hours-row">
          <input
            aria-label="Quiet hours start"
            className="push-settings__time-input"
            onChange={(e) => updateQuietHours(e.target.value, quietHoursEnd)}
            type="time"
            value={quietHoursStart}
          />
          <input
            aria-label="Quiet hours end"
            className="push-settings__time-input"
            onChange={(e) => updateQuietHours(quietHoursStart, e.target.value)}
            type="time"
            value={quietHoursEnd}
          />
        </div>
      </div>

      <div className="push-settings__actions">
        {subscribed ? (
          <>
            <TouchButton onClick={unsubscribe} variant="danger">
              Unsubscribe
            </TouchButton>
            <TouchButton
              disabled={testStatus === "sending"}
              onClick={sendTestNotification}
              variant="default"
              aria-label={
                testStatus === "sending" ? "Sending test notification" : "Send test push notification"
              }
              title={
                testStatus === "sending" ? "Sending test notification" : "Send test push notification"
              }
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
