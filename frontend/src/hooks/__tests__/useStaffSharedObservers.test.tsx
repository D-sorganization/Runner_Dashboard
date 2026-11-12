// @vitest-environment jsdom
/**
 * Board, schedule and providers are each observed by more than one component
 * (Board and the context pane; roster readiness and the context pane). They
 * must share one query key and one set of polling options so TanStack dedupes
 * them into a single request (#3672).
 */
import React from "react";
import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { staffKeys, useStaffBoard, useStaffProviders, useStaffSchedule } from "../useStaffQueries";

const api = vi.hoisted(() => ({
  fetchBoard: vi.fn(async () => ({ machines: [] })),
  fetchStaffSchedule: vi.fn(async () => ({ roles: [] })),
  fetchStaffProviders: vi.fn(async () => ({ providers: {} })),
}));

vi.mock("../../pages/Staff/staffApi", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../pages/Staff/staffApi")>()),
  fetchBoard: api.fetchBoard,
}));
vi.mock("../../pages/StaffConsole/roleContextApi", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../pages/StaffConsole/roleContextApi")>()),
  fetchStaffSchedule: api.fetchStaffSchedule,
  fetchStaffProviders: api.fetchStaffProviders,
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function Observers() {
  useStaffBoard();
  useStaffSchedule();
  useStaffProviders();
  return null;
}

const SHARED = [
  ["board", staffKeys.board(), api.fetchBoard],
  ["schedule", staffKeys.schedule(), api.fetchStaffSchedule],
  ["providers", staffKeys.providers(), api.fetchStaffProviders],
] as const;

describe("shared staff observers (#3672)", () => {
  it.each(SHARED)("two observers of %s make one request", async (_name, _key, fetcher) => {
    const client = new QueryClient();
    render(
      <QueryClientProvider client={client}>
        <Observers />
        <Observers />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it.each(SHARED)("both observers of %s carry identical polling options", async (_name, key) => {
    const client = new QueryClient();
    render(
      <QueryClientProvider client={client}>
        <Observers />
        <Observers />
      </QueryClientProvider>,
    );
    const query = client.getQueryCache().find({ queryKey: key });
    expect(query?.observers).toHaveLength(2);
    const [first, second] = (query?.observers ?? []).map((o) => o.options);
    expect(first.refetchInterval).toBeGreaterThan(0);
    for (const field of ["refetchInterval", "staleTime", "refetchIntervalInBackground"] as const) {
      expect(second[field]).toEqual(first[field]);
    }
    expect(second.queryKey).toEqual(first.queryKey);
  });
});
