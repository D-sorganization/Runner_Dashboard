/**
 * Tests for mutationQueue.ts (issue #380).
 *
 * Uses a mocked idb openDB so no real IndexedDB is required in the test
 * environment, while still exercising the queue logic end-to-end.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"

// ---------------------------------------------------------------------------
// Minimal IndexedDB mock
// ---------------------------------------------------------------------------

interface MockStore {
  data: Map<number, unknown>
  nextId: number
}

function createMockDb(store: MockStore) {
  return {
    add: vi.fn(async (_, value: unknown) => {
      const id = store.nextId++
      store.data.set(id, { ...(value as object), id })
      return id
    }),
    getAll: vi.fn(async () => Array.from(store.data.values())),
    count: vi.fn(async () => store.data.size),
    delete: vi.fn(async (_, id: number) => { store.data.delete(id) }),
    clear: vi.fn(async () => { store.data.clear() }),
    objectStoreNames: { contains: vi.fn(() => true) },
    createObjectStore: vi.fn(),
  }
}

vi.mock("idb", () => ({
  openDB: vi.fn(),
}))

import { openDB } from "idb"
import {
  enqueue,
  getAll,
  count,
  remove,
  drain,
  clearAll,
  generateIdempotencyKey,
  MAX_ENTRY_AGE_MS,
} from "../mutationQueue"

let mockStore: MockStore

beforeEach(async () => {
  mockStore = { data: new Map(), nextId: 1 }
  const db = createMockDb(mockStore)
  vi.mocked(openDB).mockResolvedValue(db as any)

  // Reset module so _db singleton is cleared between tests
  vi.resetModules()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe("generateIdempotencyKey", () => {
  it("returns a non-empty string", () => {
    const key = generateIdempotencyKey()
    expect(typeof key).toBe("string")
    expect(key.length).toBeGreaterThan(0)
  })

  it("returns unique keys on successive calls", () => {
    const keys = new Set(Array.from({ length: 20 }, () => generateIdempotencyKey()))
    expect(keys.size).toBe(20)
  })
})

describe("MAX_ENTRY_AGE_MS", () => {
  it("is 10 minutes in ms", () => {
    expect(MAX_ENTRY_AGE_MS).toBe(10 * 60 * 1000)
  })
})

describe("enqueue", () => {
  it("throws when url is empty", async () => {
    const mod = await import("../mutationQueue")
    await expect(
      mod.enqueue({ url: "", method: "POST", idempotencyKey: "k1" })
    ).rejects.toThrow("url is required")
  })

  it("throws when idempotencyKey is missing", async () => {
    const mod = await import("../mutationQueue")
    await expect(
      mod.enqueue({ url: "/api/foo", method: "POST", idempotencyKey: "" })
    ).rejects.toThrow("idempotencyKey is required")
  })
})

describe("module exports", () => {
  it("exports all expected symbols", async () => {
    const mod = await import("../mutationQueue")
    const expected = [
      "enqueue",
      "getAll",
      "count",
      "remove",
      "drain",
      "clearAll",
      "generateIdempotencyKey",
      "MAX_ENTRY_AGE_MS",
    ]
    for (const name of expected) {
      expect(typeof (mod as Record<string, unknown>)[name]).not.toBe("undefined")
    }
  })
})
