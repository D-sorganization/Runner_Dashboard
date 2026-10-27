# RD-2986 Visual Evidence

These captures compare the base layout with the candidate for Fleet Command. They were taken from the local Vite source at a 1280×844 desktop viewport (captured content cropped to 1280×780 to omit the local status footer) and a full 390×844 phone viewport.

The browser context blocked service workers and intercepted API traffic with hermetic fixtures. Priorities, meetings, directives, and proposals return empty successful responses; the capture does not read or write backend state. The JSON file aggregates request/response counts and failures by capture phase, viewport, method, path, and status (or failure reason). Observed `net::ERR_ABORTED` API requests remain included. These screenshots show the local candidate UI with fixtures and do not establish deployed behavior.

The before images come from the unchanged base worktree at `6eb4b84f87af3f695443dbcaf3e78a5194f10fc5`; the after images come from the candidate worktree. Fleet Command retains its existing 16px page margin; the candidate adds no second inset. The 375px/390px regression checks verify each Fleet Command subtab becomes visible when selected, has a minimum 44px target, and does not cause horizontal page scrolling. The heading inset assertion requires a structural heading inside the mounted Fleet overview region, plus visible headings on Fleet Command, Agent bus, Staff home, and the Ask Barb dialog. Queue has no semantic heading in its current mobile view; its visible Queue health summary landmark is required and its horizontal layout is checked, but no heading inset is claimed for Queue.

| View                       | Before                                       | After                                       |
| -------------------------- | -------------------------------------------- | ------------------------------------------- |
| Desktop, 1280×844 viewport | `rd2986-before-1280-staff-fleet-command.png` | `rd2986-after-1280-staff-fleet-command.png` |
| Phone, 390×844             | `rd2986-before-390-staff-fleet-command.png`  | `rd2986-after-390-staff-fleet-command.png`  |

`rd2986-capture-diagnostics.json` contains the API path/status receipt for the four captures.

Validation on Node v20.20.2: all four focused Playwright cases pass at 375px and 390px. The full Vitest suite passes on both base and candidate: 221 files passed, 1,806 passed, 1 skipped (1,807 total). The CI runtime version and test counts match for both revisions.
