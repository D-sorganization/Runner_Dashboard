import { describe, expect, it } from 'vitest';
import { findUndefinedUsedCssVars } from './cssVarCatalog';
import { getFleetThemeIds, fleetThemeToCssVars, FLEET_THEMES } from '../fleetThemes';

/**
 * Issue #3174 — every var(--*) under frontend/src must resolve for light and dark.
 * Runtime themes inject vars via fleetThemeToCssVars; CSS files and elevation
 * tokens cover the rest (e.g. layout vars scoped on a parent selector).
 */
describe('CSS custom property coverage', () => {
  it('does not reference undefined custom properties in frontend/src', () => {
    const missing = findUndefinedUsedCssVars();
    expect(missing).toEqual([]);
  });

  it('emits semantic alias vars for every fleet theme', () => {
    const required = [
      '--border-color',
      '--text-link',
      '--bg-input',
      '--card-bg',
      '--fg-muted',
    ];
    for (const id of getFleetThemeIds()) {
      const vars = fleetThemeToCssVars(FLEET_THEMES[id]);
      for (const key of required) {
        expect(vars[key], `${id} missing ${key}`).toBeTruthy();
      }
    }
  });
});
