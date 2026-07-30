export function createVisibleInterval(callback: () => void, intervalMs: number): () => void {
  function runIfVisible() {
    if (typeof document !== "undefined" && document.hidden) {
      return;
    }
    callback();
  }

  function onVisibilityChange() {
    if (typeof document !== "undefined" && !document.hidden) {
      callback();
    }
  }

  const timer = setInterval(runIfVisible, intervalMs);
  if (typeof document !== "undefined") {
    document.addEventListener("visibilitychange", onVisibilityChange);
  }

  return function cleanupVisibleInterval() {
    clearInterval(timer);
    if (typeof document !== "undefined") {
      document.removeEventListener("visibilitychange", onVisibilityChange);
    }
  };
}
