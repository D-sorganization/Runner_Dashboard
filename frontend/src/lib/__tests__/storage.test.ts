import { describe, it, expect, beforeEach, vi } from 'vitest';
import { z } from 'zod';
import { storage, STORAGE_KEYS, StorageError } from '../storage';

describe('storage', () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    // Reset any module-level state if needed
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

  it('should fall back to default on invalid JSON', () => {
    const key = STORAGE_KEYS.ISSUES_SOURCE_FILTER;
    localStorage.setItem(key.key, 'invalid json');
    
    const result = storage.getItem(key, TestSchema, { name: 'default', count: 0 });
    expect(result).toEqual({ name: 'default', count: 0 });
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
      storage.setItem(key, TestSchema, { name: 'test', count: 'not a number' } as any);
    }).toThrow(StorageError);
  });

  it('should handle quota exceeded by falling back to memory', () => {
    const key = STORAGE_KEYS.ISSUES_SOURCE_FILTER;
    const quotaError = new Error('Quota exceeded');
    quotaError.name = 'QuotaExceededError';
    
    // Mock localStorage.setItem to throw quota error
    const originalSetItem = localStorage.setItem.bind(localStorage);
    let callCount = 0;
    localStorage.setItem = vi.fn((k: string, v: string) => {
      callCount++;
      if (callCount === 1) {
        throw quotaError;
      }
      return originalSetItem(k, v);
    });
    
    const value = { name: 'test', count: 42 };
    expect(() => {
      storage.setItem(key, TestSchema, value);
    }).toThrow(StorageError);
    
    // Memory fallback should still work
    const result = storage.getItem(key, TestSchema, { name: 'default', count: 0 });
    expect(result).toEqual(value);
    
    // Restore
    localStorage.setItem = originalSetItem;
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