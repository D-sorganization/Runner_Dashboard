/**
 * The Staff Console e2e CI retry is reserved for one environmental failure (#3861).
 *
 * Chromium cancels every in-flight request when the host's network interfaces
 * change, and fleet runners share hosts whose Docker/veth/Tailscale interfaces
 * come and go. Against the Vite dev server that aborts lazy module loads, and the
 * tab sticks on "Failed to fetch dynamically imported module" until the test
 * times out. Any other failure must fail on its first attempt.
 */

import { join } from "node:path";

export const NETWORK_CHANGED = "net::ERR_NETWORK_CHANGED";

export function isNetworkChanged(errorText: string | undefined): boolean {
  return errorText === NETWORK_CHANGED;
}

/** Attempt 0 always runs; a retry runs only after a recorded network-change abort. */
export function retryAllowed(retry: number, markerExists: boolean): boolean {
  if (!Number.isInteger(retry) || retry < 0) throw new RangeError(`retry must be a non-negative integer, got ${retry}`);
  return retry === 0 || markerExists;
}

/** Marker written when an attempt failed after a network-change abort. */
export function networkChangedMarker(outputRoot: string, testId: string): string {
  if (!testId) throw new RangeError("testId must be non-empty");
  return join(outputRoot, ".network-changed", testId);
}
