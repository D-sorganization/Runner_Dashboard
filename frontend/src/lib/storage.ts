import { z, ZodType } from 'zod';

/**
 * Storage key registry with versioning for migration support.
 */
export const STORAGE_KEYS = {
  ISSUES_SOURCE_FILTER: { key: 'issuesSourceFilter', version: 1 },
  ISSUES_FILTER_REPO: { key: 'issues:filter_repo', version: 1 },
  ISSUES_FILTER_COMPLEXITY: { key: 'issues:filter_complexity', version: 1 },
  ISSUES_FILTER_JUDGEMENT: { key: 'issues:filter_judgement', version: 1 },
  ISSUES_FILTER_PICKABLE: { key: 'issues:filter_pickable', version: 1 },
  REMEDIATION_SUBTAB: { key: 'remediation-subtab', version: 1 },
  WORKFLOWS_MOBILE_FILTERS: { key: 'workflowsMobileFilters', version: 1, session: true },
  MAXWELL_CHAT_HISTORY: { key: 'maxwellMobileChatHistory', version: 1, session: true },
  ASST_OPEN: { key: 'ASST_LS.open', version: 1 },
  ASST_POSITION: { key: 'ASST_LS.position', version: 1 },
  ASST_WIDTH: { key: 'ASST_LS.width', version: 1 },
  ASST_TRANSCRIPT: { key: 'ASST_LS.transcript', version: 2 },
  ASST_OPEN_BY_DEFAULT: { key: 'ASST_LS.openByDefault', version: 1 },
} as const;

export type StorageKeyDef = typeof STORAGE_KEYS[keyof typeof STORAGE_KEYS];

export type StorageKey = StorageKeyDef['key'];

/**
 * In-memory fallback when localStorage is unavailable or quota exceeded.
 */
const memoryStore = new Map<string, unknown>();

/**
 * Check if we're in a private browsing mode where storage may fail.
 */
function isStorageAvailable(): boolean {
  try {
    const test = '__storage_test__';
    localStorage.setItem(test, test);
    localStorage.removeItem(test);
    return true;
  } catch {
    return false;
  }
}

function getStorage(session: boolean): Storage {
  if (session) {
    return sessionStorage;
  }
  return localStorage;
}

export class StorageError extends Error {
  constructor(
    message: string,
    public readonly code: 'QUOTA_EXCEEDED' | 'INVALID_DATA' | 'MIGRATION_FAILED' | 'STORAGE_UNAVAILABLE'
  ) {
    super(message);
    this.name = 'StorageError';
  }
}

/**
 * Typed storage helper with schema validation, migration, and quota handling.
 */
export const storage = {
  /**
   * Read an item from storage with schema validation.
   * Falls back to in-memory store if storage is unavailable.
   */
  getItem<T>(keyDef: StorageKeyDef, schema: ZodType<T>, defaultValue: T): T {
    try {
      const store = getStorage(keyDef.session ?? false);
      const raw = store.getItem(keyDef.key);

      if (raw === null) {
        return memoryStore.get(keyDef.key) as T ?? defaultValue;
      }

      let parsed: unknown;
      try {
        parsed = JSON.parse(raw);
      } catch {
        throw new StorageError(`Invalid JSON for key ${keyDef.key}`, 'INVALID_DATA');
      }

      // TODO: Apply migration if version mismatch
      const result = schema.safeParse(parsed);
      if (!result.success) {
        console.warn(`[storage] Schema validation failed for ${keyDef.key}, using default`, result.error);
        return defaultValue;
      }

      return result.data;
    } catch (err) {
      if (err instanceof StorageError) {
        throw err;
      }
      if (err instanceof Error && err.name === 'QuotaExceededError') {
        console.warn(`[storage] Quota exceeded for ${keyDef.key}, falling back to memory`);
        return memoryStore.get(keyDef.key) as T ?? defaultValue;
      }
      console.warn(`[storage] Error reading ${keyDef.key}:`, err);
      return memoryStore.get(keyDef.key) as T ?? defaultValue;
    }
  },

  /**
   * Write an item to storage with schema validation.
   * Falls back to in-memory store on quota exceeded.
   */
  setItem<T>(keyDef: StorageKeyDef, schema: ZodType<T>, value: T): void {
    // Validate before storing
    const result = schema.safeParse(value);
    if (!result.success) {
      throw new StorageError(
        `Validation failed for ${keyDef.key}: ${result.error.message}`,
        'INVALID_DATA'
      );
    }

    try {
      const store = getStorage(keyDef.session ?? false);
      const serialized = JSON.stringify(value);
      store.setItem(keyDef.key, serialized);
      // Clean up memory store if we succeeded
      memoryStore.delete(keyDef.key);
    } catch (err) {
      if (err instanceof Error && (err.name === 'QuotaExceededError' || err.message.includes('quota'))) {
        console.warn(`[storage] Quota exceeded for ${keyDef.key}, using memory fallback`);
        memoryStore.set(keyDef.key, value);
        throw new StorageError(
          `Storage quota exceeded for ${keyDef.key}. Data will be lost on page reload.`,
          'QUOTA_EXCEEDED'
        );
      }
      throw err;
    }
  },

  /**
   * Remove an item from storage.
   */
  removeItem(keyDef: StorageKeyDef): void {
    try {
      const store = getStorage(keyDef.session ?? false);
      store.removeItem(keyDef.key);
      memoryStore.delete(keyDef.key);
    } catch (err) {
      console.warn(`[storage] Error removing ${keyDef.key}:`, err);
    }
  },

  /**
   * Clear all chat history related storage.
   */
  clearChatHistory(): void {
    const chatKeys = [
      STORAGE_KEYS.MAXWELL_CHAT_HISTORY,
      STORAGE_KEYS.ASST_TRANSCRIPT,
    ];
    chatKeys.forEach((keyDef) => {
      this.removeItem(keyDef);
    });
  },

  /**
   * Check if storage is available (not in private mode).
   */
  isAvailable(): boolean {
    return isStorageAvailable();
  },

  /**
   * Get all memory fallback keys (for debugging).
   */
  getMemoryFallbackKeys(): string[] {
    return Array.from(memoryStore.keys());
  },
};