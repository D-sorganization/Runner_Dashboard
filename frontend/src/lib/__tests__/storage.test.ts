import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { z } from 'zod';
import { storage, STORAGE_KEYS, StorageError } from '../storage';

describe('storage', () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    // Reset any module-level state if needed
  });

  afterEach(() => {
    // Restore any mocks
    vi.restoreAllMocks();
  });

  const TestSchema = z.object({
    name: z.string(),
    count: z.number(),
  });

  it('should store and retrieve valid data', () => {
    const key = STORAGE_KEYS.ISSUES_SOURCE_FILTER;
    const value = { name: 'test', count: 42 };
    
    storage.setItem(key, TestSchema, value);
    const result = storage.getItem(key, TestSchema, { name: 'default', count: 0 });
    
    expect(result).toEqual(value);
  });

  it('should throw StorageError on invalid JSON', () => {
    const key = STORAGE_KEYS.ISSUES_SOURCE_FILTER;
    localStorage.setItem(key.key, 'invalid json');

    // Corrupted (non-JSON) data is surfaced as a StorageError rather than
    // silently masked — only well-formed-but-schema-invalid data falls back.
    expect(() => {
      storage.getItem(key, TestSchema, { name: 'default', count: 0 });
    }).toThrow(StorageError);
  });

  it('should fall back to default on schema mismatch', () => {
    const key = STORAGE_KEYS.ISSUES_SOURCE_FILTER;
    localStorage.setItem(key.key, JSON.stringify({ name: 123, count: 'not a number' }));
    
    const result = storage.getItem(key, TestSchema, { name: 'default', count: 0 });
    expect(result).toEqual({ name: 'default', count: 0 });
  });

  it('should throw on invalid data when setting', () => {
    const key = STORAGE_KEYS.ISSUES_SOURCE_FILTER;
    expect(() => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      storage.setItem(key, TestSchema, { name: 'test', count: 'not a number' } as any);
    }).toThrow(StorageError);
  });

  it('should handle quota exceeded by falling back to memory', () => {
    const key = STORAGE_KEYS.ISSUES_SOURCE_FILTER;
    const quotaError = new Error('Quota exceeded');
    quotaError.name = 'QuotaExceededError';

    // Swap in a fully-controlled localStorage stub for the duration of this
    // test. The runtime's real localStorage backing varies by environment —
    // jsdom's polyfill locally vs Node's built-in localStorage in CI, whose
    // native setItem cannot be reassigned or spied — so replacing the whole
    // object via vi.stubGlobal is the only env-independent way to force the
    // quota-exceeded path. The first write throws; later writes succeed.
    const backing = new Map<string, string>();
    let firstWrite = true;
    const stub: Storage = {
      get length() {
        return backing.size;
      },
      clear: () => backing.clear(),
      getItem: (k: string) => backing.get(k) ?? null,
      key: (i: number) => Array.from(backing.keys())[i] ?? null,
      removeItem: (k: string) => backing.delete(k),
      setItem: (k: string, v: string) => {
        if (firstWrite) {
          firstWrite = false;
          throw quotaError;
        }
        backing.set(k, v);
      },
    };
    vi.stubGlobal('localStorage', stub);

    try {
      const value = { name: 'test', count: 42 };
      expect(() => {
        storage.setItem(key, TestSchema, value);
      }).toThrow(StorageError);

      // The failed write routes the value into the in-memory fallback store,
      // so a subsequent read still surfaces it.
      const result = storage.getItem(key, TestSchema, { name: 'default', count: 0 });
      expect(result).toEqual(value);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('should remove items correctly', () => {
    const key = STORAGE_KEYS.ISSUES_SOURCE_FILTER;
    const value = { name: 'test', count: 42 };
    
    storage.setItem(key, TestSchema, value);
    storage.removeItem(key);
    
    const result = storage.getItem(key, TestSchema, { name: 'default', count: 0 });
    expect(result).toEqual({ name: 'default', count: 0 });
  });

  it('should use sessionStorage when specified', () => {
    const key = STORAGE_KEYS.WORKFLOWS_MOBILE_FILTERS;
    const value = { name: 'test', count: 42 };
    
    storage.setItem(key, TestSchema, value);
    
    // Should be in sessionStorage, not localStorage
    expect(sessionStorage.getItem(key.key)).not.toBeNull();
    expect(localStorage.getItem(key.key)).toBeNull();
  });

  it('should return default value when key does not exist', () => {
    const key = STORAGE_KEYS.ISSUES_SOURCE_FILTER;
    const defaultValue = { name: 'default', count: 0 };
    
    const result = storage.getItem(key, TestSchema, defaultValue);
    expect(result).toEqual(defaultValue);
  });

  it('should check storage availability', () => {
    expect(storage.isAvailable()).toBe(true);
  });
});
