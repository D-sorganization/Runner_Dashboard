import { readFileSync, readdirSync, statSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { elevationCssVars } from '../tokens';
import { FLEET_THEMES, fleetThemeToCssVars } from '../fleetThemes';

const SRC_ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '../..');

const USE_RE = /var\(\s*(--[a-zA-Z0-9_-]+)/g;
const DEF_RE = /(?:^|[\s{,])(--[a-zA-Z0-9_-]+)\s*:/g;

/** Strip comments so documentation placeholders (e.g. var(--space-N)) are ignored. */
function stripCssAndJsComments(text: string): string {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:])\/\/.*$/gm, '$1');
}

function walkSourceFiles(dir: string, acc: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const full = path.join(dir, name);
    const st = statSync(full);
    if (st.isDirectory()) {
      if (name === 'node_modules' || name === 'dist') continue;
      walkSourceFiles(full, acc);
    } else if (/\.(tsx?|css)$/.test(name)) {
      acc.push(full);
    }
  }
  return acc;
}

/** Every `var(--*)` reference under frontend/src. */
export function collectUsedCssVars(): Set<string> {
  const used = new Set<string>();
  for (const file of walkSourceFiles(SRC_ROOT)) {
    const text = stripCssAndJsComments(readFileSync(file, 'utf8'));
    let m: RegExpExecArray | null;
    USE_RE.lastIndex = 0;
    while ((m = USE_RE.exec(text)) !== null) {
      used.add(m[1]);
    }
  }
  return used;
}

/** Declarations from CSS files plus typed token maps and fleet theme runtime output. */
export function collectDefinedCssVars(): Set<string> {
  const defined = new Set<string>(Object.keys(elevationCssVars));

  for (const file of walkSourceFiles(SRC_ROOT)) {
    if (!file.endsWith('.css')) continue;
    const text = readFileSync(file, 'utf8');
    let m: RegExpExecArray | null;
    DEF_RE.lastIndex = 0;
    while ((m = DEF_RE.exec(text)) !== null) {
      defined.add(m[1]);
    }
  }

  for (const key of Object.keys(fleetThemeToCssVars(FLEET_THEMES.dark))) {
    defined.add(key);
  }

  return defined;
}

/** Vars referenced in source but not declared anywhere in the catalog. */
export function findUndefinedUsedCssVars(): string[] {
  const used = collectUsedCssVars();
  const defined = collectDefinedCssVars();
  const missing: string[] = [];
  for (const name of used) {
    if (!defined.has(name)) missing.push(name);
  }
  return missing.sort();
}
