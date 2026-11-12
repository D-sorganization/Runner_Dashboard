/**
 * The CI retry is reserved for Chromium's host network-change abort (#3861).
 *
 * Pure decision logic only: Playwright cannot synthesise ERR_NETWORK_CHANGED
 * (route.abort has no such code), so the fixture wiring is proven by these
 * rules plus the CI annotation on any retried test.
 */

import { join } from "node:path";
import { expect, test } from "./fixtures";
import { NETWORK_CHANGED, isNetworkChanged, networkChangedMarker, retryAllowed } from "./networkChangedRetry";

test("only Chromium's network-change abort counts", () => {
  expect(isNetworkChanged(NETWORK_CHANGED)).toBe(true);
  expect(isNetworkChanged("net::ERR_ABORTED")).toBe(false);
  expect(isNetworkChanged("net::ERR_CONNECTION_REFUSED")).toBe(false);
  expect(isNetworkChanged(undefined)).toBe(false);
});

test("the first attempt always runs and a retry needs the marker", () => {
  expect(retryAllowed(0, false)).toBe(true);
  expect(retryAllowed(0, true)).toBe(true);
  expect(retryAllowed(1, true)).toBe(true);
  expect(retryAllowed(1, false)).toBe(false);
});

test("retry counts outside the contract are rejected", () => {
  expect(() => retryAllowed(-1, true)).toThrow(RangeError);
  expect(() => retryAllowed(0.5, true)).toThrow(RangeError);
});

test("the marker is keyed by test id under the output root", () => {
  expect(networkChangedMarker("/out", "abc-123")).toBe(join("/out", ".network-changed", "abc-123"));
  expect(() => networkChangedMarker("/out", "")).toThrow(RangeError);
});
