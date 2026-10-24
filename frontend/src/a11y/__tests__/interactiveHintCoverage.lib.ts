/**
 * Static audit: interactive controls and status badges must expose a hover/focus
 * hint via Tooltip, title, or aria-label (Dieter / fleet a11y epic follow-on).
 */
import fs from "node:fs";
import path from "node:path";
import ts from "typescript";
import { shouldSkipHintCoverageFile } from "./interactiveHintExclusions";

export type InteractiveKind =
  | "button"
  | "link"
  | "input"
  | "select"
  | "textarea"
  | "role-button"
  | "role-tab"
  | "role-switch"
  | "role-menuitem"
  | "role-checkbox"
  | "role-radio"
  | "status-badge";

export interface HintViolation {
  file: string;
  line: number;
  kind: InteractiveKind;
  snippet: string;
}

export interface HintCoverageReport {
  scannedFiles: number;
  totalInteractive: number;
  withHint: number;
  violations: HintViolation[];
}

const INTERACTIVE_ROLES = new Set([
  "button",
  "tab",
  "switch",
  "menuitem",
  "checkbox",
  "radio",
]);

const STATUS_BADGE_CLASS = /\b(badge|pill)\b/i;

function isDeprecatedFile(source: string): boolean {
  const head = source.slice(0, 800);
  return /@deprecated\b/i.test(head);
}

function jsxTagName(name: ts.JsxTagNameExpression): string {
  if (ts.isIdentifier(name)) return name.text;
  if (ts.isPropertyAccessExpression(name)) return name.name.text;
  return "";
}

function attrMap(attrs: ts.JsxAttributes): Map<string, string | true> {
  const map = new Map<string, string | true>();
  for (const prop of attrs.properties) {
    if (!ts.isJsxAttribute(prop) || !prop.name) continue;
    const key = ts.isIdentifier(prop.name) ? prop.name.text : prop.name.getText();
    if (!prop.initializer) {
      map.set(key, true);
      continue;
    }
    if (ts.isStringLiteral(prop.initializer)) {
      map.set(key, prop.initializer.text);
      continue;
    }
    if (ts.isJsxExpression(prop.initializer) && prop.initializer.expression) {
      const expr = prop.initializer.expression;
      if (ts.isStringLiteral(expr)) {
        map.set(key, expr.text);
      } else {
        map.set(key, true);
      }
    }
  }
  return map;
}

function hasNonEmptyAttr(map: Map<string, string | true>, key: string): boolean {
  const v = map.get(key);
  if (v === undefined) return false;
  if (v === true) return true;
  return v.trim().length > 0;
}

function jsxTextFromChildren(children: ts.NodeArray<ts.JsxChild>): string {
  const parts: string[] = [];
  for (const child of children) {
    if (ts.isJsxText(child)) {
      parts.push(child.text);
    } else if (ts.isJsxExpression(child) && child.expression) {
      if (ts.isStringLiteral(child.expression)) parts.push(child.expression.text);
      else if (ts.isNoSubstitutionTemplateLiteral(child.expression)) {
        parts.push(child.expression.text);
      }
    } else if (ts.isJsxElement(child)) {
      parts.push(jsxTextFromChildren(child.children));
    } else if (ts.isJsxFragment(child)) {
      parts.push(jsxTextFromChildren(child.children));
    }
  }
  return parts.join(" ").replace(/\s+/g, " ").trim();
}

function jsxTextContent(node: ts.JsxElement | ts.JsxSelfClosingElement): string {
  if (ts.isJsxSelfClosingElement(node)) return "";
  return jsxTextFromChildren(node.children);
}

function collectLabelForIds(sourceFile: ts.SourceFile): Set<string> {
  const ids = new Set<string>();
  const visit = (node: ts.Node) => {
    if (ts.isJsxElement(node) || ts.isJsxSelfClosingElement(node)) {
      const opening = ts.isJsxElement(node) ? node.openingElement : node;
      const tag = jsxTagName(opening.tagName);
      if (tag === "label" || tag === "Label") {
        const map = attrMap(opening.attributes);
        const htmlFor = map.get("htmlFor") ?? map.get("for");
        if (typeof htmlFor === "string" && htmlFor.trim()) ids.add(htmlFor.trim());
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(sourceFile);
  return ids;
}

function classifyInteractive(
  tag: string,
  map: Map<string, string | true>,
  className: string | true | undefined,
): InteractiveKind | null {
  const role = map.get("role");
  const roleStr = typeof role === "string" ? role : "";

  if (tag === "button" || tag === "TouchButton") return "button";
  if (tag === "a" && hasNonEmptyAttr(map, "href")) return "link";
  if (tag === "input") {
    const type = map.get("type");
    if (type === "hidden") return null;
    return "input";
  }
  if (tag === "select") return "select";
  if (tag === "textarea") return "textarea";

  if (typeof role === "string" && INTERACTIVE_ROLES.has(roleStr)) {
    if (roleStr === "tab") return "role-tab";
    if (roleStr === "switch") return "role-switch";
    if (roleStr === "menuitem") return "role-menuitem";
    if (roleStr === "checkbox") return "role-checkbox";
    if (roleStr === "radio") return "role-radio";
    return "role-button";
  }

  const cls = typeof className === "string" ? className : "";
  if (
    (tag === "span" || tag === "div") &&
    !map.has("aria-hidden") &&
    (roleStr === "status" || STATUS_BADGE_CLASS.test(cls))
  ) {
    return "status-badge";
  }

  return null;
}

function elementHasHint(
  map: Map<string, string | true>,
  text: string,
  parentTooltip: boolean,
  labelForIds: Set<string>,
): boolean {
  if (parentTooltip) return true;
  if (hasNonEmptyAttr(map, "aria-label")) return true;
  if (hasNonEmptyAttr(map, "title")) return true;
  if (hasNonEmptyAttr(map, "aria-labelledby")) return true;
  if (hasNonEmptyAttr(map, "data-hint-exempt")) return true;
  if (text.length > 0) return true;
  const id = map.get("id");
  if (typeof id === "string" && labelForIds.has(id)) return true;
  return false;
}

function lineOf(sourceFile: ts.SourceFile, node: ts.Node): number {
  return sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile)).line + 1;
}

function snippetOf(sourceFile: ts.SourceFile, node: ts.Node, max = 72): string {
  const text = node.getText(sourceFile).replace(/\s+/g, " ").trim();
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

function walkJsx(
  node: ts.Node,
  sourceFile: ts.SourceFile,
  fileRel: string,
  parentTooltip: boolean,
  parentIsLabel: boolean,
  labelForIds: Set<string>,
  violations: HintViolation[],
  counts: { total: number; withHint: number },
): void {
  if (ts.isJsxElement(node) || ts.isJsxSelfClosingElement(node)) {
    const opening = ts.isJsxElement(node) ? node.openingElement : node;
    const tag = jsxTagName(opening.tagName);
    const map = attrMap(opening.attributes);
    const classAttr = map.get("className");
    const kind = classifyInteractive(tag, map, classAttr);
    const inTooltip = parentTooltip || tag === "Tooltip";
    const text = ts.isJsxElement(node) ? jsxTextContent(node) : "";
    const isLabelTag = tag === "label" || tag === "Label";

    // TouchButton/Pill forward aria-label/title via prop spread — callers are audited.
    if (
      kind === "button" &&
      (fileRel === "primitives/TouchButton.tsx" || fileRel === "primitives/Pill.tsx")
    ) {
      return;
    }

    if (kind) {
      counts.total += 1;
      if (elementHasHint(map, text, inTooltip || parentIsLabel, labelForIds)) {
        counts.withHint += 1;
      } else {
        violations.push({
          file: fileRel,
          line: lineOf(sourceFile, opening),
          kind,
          snippet: snippetOf(sourceFile, opening),
        });
      }
    }

    const childParentTooltip = inTooltip;
    const childParentLabel = parentIsLabel || isLabelTag;
    if (ts.isJsxElement(node)) {
      for (const child of node.children) {
        walkJsx(
          child,
          sourceFile,
          fileRel,
          childParentTooltip,
          childParentLabel,
          labelForIds,
          violations,
          counts,
        );
      }
    }
    return;
  }

  ts.forEachChild(node, (child) =>
    walkJsx(child, sourceFile, fileRel, parentTooltip, parentIsLabel, labelForIds, violations, counts),
  );
}

function collectTsxFiles(rootDir: string): string[] {
  const out: string[] = [];
  function walk(dir: string) {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        walk(full);
      } else if (entry.isFile() && entry.name.endsWith(".tsx")) {
        out.push(full);
      }
    }
  }
  walk(rootDir);
  return out;
}

export function auditInteractiveHints(frontendSrcRoot: string): HintCoverageReport {
  const violations: HintViolation[] = [];
  let scannedFiles = 0;
  const counts = { total: 0, withHint: 0 };

  for (const abs of collectTsxFiles(frontendSrcRoot)) {
    const rel = path.relative(frontendSrcRoot, abs).replace(/\\/g, "/");
    if (shouldSkipHintCoverageFile(rel)) continue;

    const source = fs.readFileSync(abs, "utf8");
    if (isDeprecatedFile(source)) continue;

    scannedFiles += 1;
    const sourceFile = ts.createSourceFile(abs, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    const labelForIds = collectLabelForIds(sourceFile);
    walkJsx(sourceFile, sourceFile, rel, false, false, labelForIds, violations, counts);
  }

  return {
    scannedFiles,
    totalInteractive: counts.total,
    withHint: counts.withHint,
    violations,
  };
}

export function formatCoverageRatio(report: HintCoverageReport): string {
  if (report.totalInteractive === 0) return "0/0 (n/a)";
  const pct = Math.round((report.withHint / report.totalInteractive) * 1000) / 10;
  return `${report.withHint}/${report.totalInteractive} (${pct}%)`;
}

/**
 * Globs (path suffixes) that must be violation-free. Grows one area per PR.
 */
export const HINT_COVERAGE_ENFORCED_SUFFIXES: readonly string[] = [
  "/shell/DesktopShell.tsx",
  "/shell/Sidebar.tsx",
  "/shell/TopToolstrip.tsx",
  "/shell/HelpAbout.tsx",
  "/shell/BarbSidebar.tsx",
];

/** Path prefixes that must be violation-free (grows one dashboard area per PR). */
export const HINT_COVERAGE_ENFORCED_PREFIXES: readonly string[] = [
  "pages/Staff/",
  "pages/StaffConsole/",
];

/** When true, the audit test requires zero violations across all scanned TSX. */
export const HINT_COVERAGE_ENFORCE_FLEET_WIDE = false;

export function violationsInEnforcedAreas(report: HintCoverageReport): HintViolation[] {
  if (HINT_COVERAGE_ENFORCE_FLEET_WIDE) return report.violations;
  return report.violations.filter((v) => {
    if (HINT_COVERAGE_ENFORCED_SUFFIXES.some((suffix) => v.file.endsWith(suffix))) return true;
    if (HINT_COVERAGE_ENFORCED_PREFIXES.some((prefix) => v.file.startsWith(prefix))) return true;
    return false;
  });
}
