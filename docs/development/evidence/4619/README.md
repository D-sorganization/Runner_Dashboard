# Active Work Panel UI Verification Evidence (#4619, PR #4621)

This directory contains hermetic browser evidence verifying the Active Work panel completeness indicators at desktop width 1280 and phone 390x844 across degraded and healthy states.

## Test Matrix

- **Viewports:**
  - Desktop: 1280x800
  - Mobile: 390x844
- **States:**
  1. `partial-staff-read`: `complete: true`, `staff_complete: false` (peer staff read timed out or failed; board history intact)
  2. `partial-board-read`: `complete: false`, `staff_complete: true` (board history read timed out or failed)
  3. `healthy`: `complete: true`, `staff_complete: true` (all peer and board reads healthy)

## Verification Results

| Viewport          | State              | Before (`aaa7069e`)        | After (`b453c67c`)                              | Badge in After       |
| :---------------- | :----------------- | :------------------------- | :---------------------------------------------- | :------------------- |
| **Desktop 1280**  | Partial staff read | No warning badge (omitted) | **"partial staff read"** warning badge rendered | `partial staff read` |
| **Desktop 1280**  | Partial board read | "partial board read" badge | "partial board read" badge preserved            | `partial board read` |
| **Desktop 1280**  | Healthy            | No warning badge           | No warning badge                                | none                 |
| **Phone 390x844** | Partial staff read | No warning badge (omitted) | **"partial staff read"** warning badge rendered | `partial staff read` |
| **Phone 390x844** | Partial board read | "partial board read" badge | "partial board read" badge preserved            | `partial board read` |
| **Phone 390x844** | Healthy            | No warning badge           | No warning badge                                | none                 |

## Artifact Files

### 1. Partial Staff Read (`complete: true`, `staff_complete: false`)

- Desktop (1280x800):
  - Before: `before-desktop-1280-partial-staff-read.png` (Panel: `before-desktop-1280-partial-staff-read-panel.png`)
  - After: `after-desktop-1280-partial-staff-read.png` (Panel: `after-desktop-1280-partial-staff-read-panel.png`)
- Phone (390x844):
  - Before: `before-phone-390x844-partial-staff-read.png` (Panel: `before-phone-390x844-partial-staff-read-panel.png`)
  - After: `after-phone-390x844-partial-staff-read.png` (Panel: `after-phone-390x844-partial-staff-read-panel.png`)

### 2. Partial Board Read (`complete: false`, `staff_complete: true`)

- Desktop (1280x800):
  - Before: `before-desktop-1280-partial-board-read.png` (Panel: `before-desktop-1280-partial-board-read-panel.png`)
  - After: `after-desktop-1280-partial-board-read.png` (Panel: `after-desktop-1280-partial-board-read-panel.png`)
- Phone (390x844):
  - Before: `before-phone-390x844-partial-board-read.png` (Panel: `before-phone-390x844-partial-board-read-panel.png`)
  - After: `after-phone-390x844-partial-board-read.png` (Panel: `after-phone-390x844-partial-board-read-panel.png`)

### 3. Healthy (`complete: true`, `staff_complete: true`)

- Desktop (1280x800):
  - Before: `before-desktop-1280-healthy.png` (Panel: `before-desktop-1280-healthy-panel.png`)
  - After: `after-desktop-1280-healthy.png` (Panel: `after-desktop-1280-healthy-panel.png`)
- Phone (390x844):
  - Before: `before-phone-390x844-healthy.png` (Panel: `before-phone-390x844-healthy-panel.png`)
  - After: `after-phone-390x844-healthy.png` (Panel: `after-phone-390x844-healthy-panel.png`)

## Sanitization

All fixtures use sanitized synthetic records with generic machine roles, local session keys, and no Tailscale IPs, hostnames, or private deployment identifiers.
