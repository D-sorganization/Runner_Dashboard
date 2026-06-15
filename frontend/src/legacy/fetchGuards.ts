type FetchInput = Parameters<typeof fetch>[0];
type FetchOptions = Parameters<typeof fetch>[1];
type ToastHost = {
  showToast: (
    message: string,
    options: { variant: "error"; title: string },
  ) => void;
};

export const SERVICE_WORKER_CACHE_DENYLIST = [/^\/api\/credentials(?:\/|$)/];

function requestUrl(url: FetchInput): string {
  if (typeof url === "string") return url;
  if (url instanceof URL) return url.toString();
  return url.url;
}

export function shouldBypassServiceWorkerCache(url: FetchInput): boolean {
  try {
    const origin = typeof window === "undefined" ? "http://localhost" : window.location.origin;
    const parsed = new URL(requestUrl(url), origin);
    return SERVICE_WORKER_CACHE_DENYLIST.some((pattern) => pattern.test(parsed.pathname));
  } catch {
    return false;
  }
}

export type InstallFetchGuardsOptions = {
  emitSessionExpired: () => void;
  shouldIgnoreUnauthorizedResponse: (url: FetchInput) => boolean;
  tryRefreshSession: (fetchImpl: typeof fetch) => Promise<boolean>;
  fetchImpl?: typeof fetch;
  getToaster?: () => ToastHost | null | undefined;
  targetWindow?: Window & typeof globalThis;
};

export function installLegacyFetchGuards(options: InstallFetchGuardsOptions): typeof fetch {
  const targetWindow = options.targetWindow ?? window;
  const originalFetch = options.fetchImpl ?? targetWindow.fetch.bind(targetWindow);

  targetWindow.fetch = async function guardedFetch(url: FetchInput, fetchOptions?: FetchOptions) {
    const guardedOptions = shouldBypassServiceWorkerCache(url)
      ? { ...(fetchOptions || {}), cache: "no-store" as RequestCache }
      : fetchOptions;
    const resp = await originalFetch(url, guardedOptions);

    if (resp.status === 401 && !options.shouldIgnoreUnauthorizedResponse(url)) {
      console.warn("[auth] 401 Unauthorized from", url);
      if (await options.tryRefreshSession(originalFetch)) {
        return originalFetch(url, guardedOptions);
      }

      try {
        const toaster = options.getToaster
          ? options.getToaster()
          : ((targetWindow as unknown as { __toaster?: ToastHost }).__toaster);
        if (toaster && typeof toaster.showToast === "function") {
          toaster.showToast(
            "Your session has expired. Please log in again to continue.",
            { variant: "error", title: "Session expired" },
          );
        }
      } catch (toastErr) {
        console.warn("[auth] Failed to emit 401 toast:", toastErr);
      }
      options.emitSessionExpired();
    }

    return resp;
  };

  return originalFetch;
}
