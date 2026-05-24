/**
 * Tests for semantic status tokens (D5 / issue #724).
 *
 * Verifies:
 * 1. All statusToken variants have bg and fg properties.
 * 2. All radii values end with 'px'.
 * 3. All shadows are non-empty strings.
 */

import { describe, it, expect } from 'vitest';
import { statusTokens, radii, shadows } from '../design/tokens';
import type { StatusVariant } from '../design/tokens';

const EXPECTED_VARIANTS: StatusVariant[] = ['healthy', 'warning', 'critical', 'unknown', 'info'];

describe('statusTokens', () => {
  it('has all required variants', () => {
    for (const variant of EXPECTED_VARIANTS) {
      expect(statusTokens).toHaveProperty(variant);
    }
  });

  it('each variant has bg and fg string properties', () => {
    for (const variant of EXPECTED_VARIANTS) {
      const token = statusTokens[variant];
      expect(typeof token.bg).toBe('string');
      expect(typeof token.fg).toBe('string');
      expect(token.bg.length).toBeGreaterThan(0);
      expect(token.fg.length).toBeGreaterThan(0);
    }
  });

  it('bg values start with rgba(', () => {
    for (const variant of EXPECTED_VARIANTS) {
      expect(statusTokens[variant].bg).toMatch(/^rgba\(/);
    }
  });

  it('fg values are hex colors', () => {
    for (const variant of EXPECTED_VARIANTS) {
      expect(statusTokens[variant].fg).toMatch(/^#[0-9a-fA-F]+$/);
    }
  });
});

describe('radii', () => {
  it('all values end with px', () => {
    for (const [key, value] of Object.entries(radii)) {
      expect(value).toMatch(/px$/, `radii.${key} should end with 'px'`);
    }
  });

  it('has sm, md, lg, pill keys', () => {
    expect(radii).toHaveProperty('sm');
    expect(radii).toHaveProperty('md');
    expect(radii).toHaveProperty('lg');
    expect(radii).toHaveProperty('pill');
  });
});

describe('shadows', () => {
  it('all values are non-empty strings', () => {
    for (const [key, value] of Object.entries(shadows)) {
      expect(typeof value).toBe('string');
      expect(value.length).toBeGreaterThan(0, `shadows.${key} should be non-empty`);
    }
  });

  it('has soft, card, modal keys', () => {
    expect(shadows).toHaveProperty('soft');
    expect(shadows).toHaveProperty('card');
    expect(shadows).toHaveProperty('modal');
  });
});
