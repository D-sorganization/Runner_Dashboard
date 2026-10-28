// @vitest-environment node
/**
 * lazyChunks.test.ts — the Fleet and Remediation pages must stay out of the
 * eagerly loaded graph (entry chunk + its static imports). Optional telemetry
 * also stays outside that graph (#3282), so /staff and the
 * other first-load routes do not download them (#2990).
 */
import path from "node:path";
import { build } from "vite";
import { describe, expect, it } from "vitest";

type Chunk = {
  type: "chunk";
  fileName: string;
  isEntry: boolean;
  imports: string[];
  modules: Record<string, unknown>;
};

const LAZY_MODULES = [
  /lib\/webVitals\.ts$/,
  /node_modules\/web-vitals\//,
  /pages\/OverviewPage/,
  /pages\/FleetTab/,
  /pages\/RemediationPage/,
  /pages\/RemediationTab/,
  /pages\/Fleet\/Mobile/,
  /pages\/Remediation\/Mobile/,
];

describe("eager graph excludes lazy pages and optional telemetry", () => {
  it("entry chunk and its static imports contain none of the lazy pages", async () => {
    const result = await build({
      configFile: path.join(__dirname, "../../../../vite.config.ts"),
      logLevel: "silent",
      build: { write: false },
    });
    const out = ((Array.isArray(result) ? result[0] : result) as { output: Chunk[] }).output;
    const chunks = new Map(out.filter((o) => o.type === "chunk").map((c) => [c.fileName, c]));
    const entry = [...chunks.values()].find((c) => c.isEntry)!;

    const eager = new Set<string>();
    const walk = (name: string) => {
      if (eager.has(name) || !chunks.has(name)) return;
      eager.add(name);
      chunks.get(name)!.imports.forEach(walk);
    };
    walk(entry.fileName);

    const offenders = [...eager].flatMap((name) =>
      Object.keys(chunks.get(name)!.modules)
        .filter((id) => LAZY_MODULES.some((re) => re.test(id.replaceAll("\\", "/"))))
        .map((id) => `${name}: ${id}`),
    );
    expect(offenders).toEqual([]);
  }, 60_000);
});
