import "@testing-library/jest-dom/vitest";
import { configure } from "@testing-library/react";

// Loaded self-hosted runners routinely exceed the 1000ms default for findBy*/waitFor
// (#3056). Failing assertions still fail; they just get environmental headroom.
export const ASYNC_UTIL_TIMEOUT_MS = 10000;
configure({ asyncUtilTimeout: ASYNC_UTIL_TIMEOUT_MS });

function createMemoryStorage(): Storage {
  const data = new Map<string, string>();
  return {
    get length() {
      return data.size;
    },
    clear: () => data.clear(),
    getItem: (key: string) => data.get(key) ?? null,
    key: (index: number) => Array.from(data.keys())[index] ?? null,
    removeItem: (key: string) => {
      data.delete(key);
    },
    setItem: (key: string, value: string) => {
      data.set(key, value);
    },
  };
}

if (
  !globalThis.localStorage ||
  typeof globalThis.localStorage.clear !== "function"
) {
  const storage = createMemoryStorage();
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: storage,
  });
  if (typeof window !== "undefined") {
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      value: storage,
    });
  }
}

// Polyfill for matchMedia in jsdom environment
Object.defineProperty(global, "matchMedia", {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => {},
  }),
});
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  if (typeof document !== "undefined") {
    Object.defineProperty(document, "hidden", { value: false, configurable: true });
  }
});
