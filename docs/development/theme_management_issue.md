## Feature: Color Theme Management for Runner Dashboard

### Summary
Integrate the fleet-wide color theme system from the Tools repository into the Runner Dashboard, enabling users to switch between built-in themes and create custom color themes. This brings the Dashboard into parity with the shared theming infrastructure used by Gasification Model and UpstreamDrift.

---

### Motivation
- **Fleet Consistency**: Gasification Model (PyQt6) and UpstreamDrift (PyQt6) already consume the shared theme system via `ThemeManager`. The Runner Dashboard (React/TypeScript) should use the same canonical `themes.json` definitions.
- **User Preference**: Users working across multiple fleet applications expect visual consistency.
- **Custom Branding**: Custom theme creation allows teams and individuals to personalize their workspace.

---

### Shared Theme System (from Tools repo)

The Tools repo provides a comprehensive theme system at `src/shared/typescript/theme/`:

**Available Infrastructure:**
- `themeDefinitions.ts` — Loads themes from `themes.json`, generates CSS variables, WCAG contrast verification
- `themeStore.ts` — Persistent theme state management
- `themeApi.ts` — Theme API for programmatic access
- `theme-variables.css` — CSS custom property definitions
- `designTokens.ts` — Design token system

**13 Built-in Themes:**
Light, Dark, Slate Gray, Ocean Blue, Forest Green, Monokai, Dracula, One Dark, Gitpod Dark, MS Word, MS Excel, Legal Pad, High Contrast

**Sidekick Integration:**
- `generateSidekickCSSVariables()` — Generates Sidekick-specific CSS variables from any theme
- `applySidekickThemeToElement()` — Applies Sidekick theme to any HTML element

---

### Implementation Plan

#### Phase 1: Theme Integration
- [ ] Import `src/shared/typescript/theme/` package from Tools repo as a dependency
- [ ] Create `ThemeProvider` React context wrapping `themeStore`
- [ ] Apply `generateCSSVariables()` to document root on theme change
- [ ] Add theme selector dropdown to Dashboard settings/header
- [ ] Persist selected theme to `localStorage`
- [ ] Support dark/light mode auto-detection via `prefers-color-scheme`

#### Phase 2: Custom Theme Creation
- [ ] Build custom theme creation dialog with color pickers for all 14 base color keys + 8 semantic color keys
- [ ] Live preview of theme changes
- [ ] Save/load custom themes via `localStorage` or backend API
- [ ] Import/export themes as JSON (compatible with `themes.json` format)
- [ ] WCAG contrast ratio validation using `verifyThemeReadability()` from shared lib

#### Phase 3: Enhanced Theming
- [ ] Theme scheduling (auto-switch dark mode at sunset)
- [ ] Per-page theme overrides
- [ ] Theme sharing between fleet applications (sync via QSettings/localStorage bridge)

---

### Files to Create/Modify
- `frontend/src/hooks/useTheme.ts` — Theme React hook
- `frontend/src/components/ThemeProvider.tsx` — Theme context provider
- `frontend/src/components/ThemeSelector.tsx` — Theme selection UI
- `frontend/src/components/CustomThemeDialog.tsx` — Custom theme builder
- `frontend/src/index.css` — Replace hardcoded colors with CSS variables
- `package.json` — Add Tools shared theme dependency

### Acceptance Criteria
- [ ] All 13 built-in themes render correctly
- [ ] Theme selection persists across sessions
- [ ] Custom themes can be created, previewed, saved, and deleted
- [ ] All text meets WCAG AA contrast ratios (4.5:1)
- [ ] Theme changes apply globally without page reload
- [ ] Shared `themes.json` is the single source of truth
