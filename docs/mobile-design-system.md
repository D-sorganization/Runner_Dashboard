# Mobile Design System

## Scope

This document defines the mobile design token contract for the runner
dashboard. The current runtime frontend remains `frontend/index.html`; the
`frontend/src/design/` modules are static source-of-truth scaffolding for the
Vite split and are guarded by tests so future runtime migration has stable
names and values.

## Breakpoints

| Name | Width | Purpose |
| --- | ---: | --- |
| `xs` | 360 | Small Android fallback |
| `sm` | 390 | iPhone compact contract |
| `md` | 768 | Mobile/tablet split and current CSS breakpoint |
| `lg` | 1024 | Desktop shell entry |
| `xl` | 1280 | Dense operations layouts |

The epic acceptance viewports are 375x812 and 412x915. Runtime layouts must
stay usable at both sizes.

## Touch Targets

Mobile controls use `--mobile-hit-target` with a minimum target of `44px`.
High-frequency controls such as bottom navigation and primary action buttons
should prefer the `48px` comfortable target when space allows.
The HTML viewport metadata must keep user scaling enabled; do not add
`maximum-scale` or `user-scalable=no` to the mobile shell.

## Type Scale

| Token | Size | Use |
| --- | ---: | --- |
| `title` | 20px | Mobile page title |
| `sectionTitle` | 16px | Section headings and bottom-sheet titles |
| `body` | 14px | Default UI text |
| `meta` | 12px | Secondary labels and timestamps |
| `micro` | 11px | Dense badges and chips |

Do not scale type with viewport width. Long labels should wrap or truncate
inside stable containers.

## Motion

Motion uses short transitions: `120ms`, `180ms`, or `240ms`. All animation and
transition behavior must respect `prefers-reduced-motion: reduce`; static
frontend tests enforce the reduced-motion CSS and helper contract.

## Color

The mobile token map mirrors the existing dark dashboard palette in
`frontend/index.html`:

- `--bg-primary`: `#0f1117`
- `--bg-secondary`: `#161b22`
- `--bg-tertiary`: `#1c2333`
- `--bg-card`: `#1c2128`
- `--bg-hover`: `#252d3a`
- `--text-primary`: `#e6edf3`
- `--text-secondary`: `#8b949e`
- `--text-muted`: `#6e7681`
- `--accent-blue`: `#58a6ff`
- `--accent-green`: `#3fb950`
- `--accent-red`: `#f85149`
- `--accent-yellow`: `#d29922`
- `--accent-purple`: `#bc8cff`
- `--accent-orange`: `#f0883e`

Future Vite components should import from `frontend/src/design/tokens.ts`
instead of adding new inline color literals.
