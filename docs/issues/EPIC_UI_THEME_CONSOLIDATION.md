# EPIC: UI Theme Consolidation & Runner-Dashboard Customization

## Objective

Standardize the visual design across the fleet by consolidating color themes and allowing end-users to customize the UI via the `runner-dashboard`.

## Background

We need better color theme options in the runner-dashboard. We should share our color theme tool from the `Tools` repo to enable the ability to customize. The `Tools` repository recently consolidated its `theme` library, which includes robust dynamic palette generation and comprehensive design tokens. By propagating this to the runner-dashboard, we can unlock a premium, cohesive, and fully customizable visual aesthetic.

## Key Goals

1. **Integrate Tools Theme System**:
   - Refactor the `runner-dashboard` to inherit/utilize shared theme components.
   - Replace hardcoded hex colors with unified design tokens.
2. **User Customization GUI**:
   - Create a "Theme Settings" interface in the `runner-dashboard`.
   - Allow users to select primary/secondary accents, toggle Dark/Light mode, etc.
3. **Save/Load Preferences**:
   - Serialize the chosen color theme into the user's persistent preferences.
   - Ensure the theme applies instantly to all active widgets upon selection.

## Implementation Steps

- [ ] Audit `runner-dashboard` for hardcoded styles and remove them.
- [ ] Import `ThemeManager` or shared tokens.
- [ ] Create the Theme Customizer UI component.
- [ ] Implement persistent state saving for the selected theme.
