// Tests for labels-sync.js
// Run with: node --test .github/scripts/labels-sync.test.js
const { describe, it } = require("node:test");
const assert = require("node:assert");

// Mock js-yaml before requiring the module under test
const mockManifest = [
  { name: "bug", color: "d73a4a", description: "Something is broken" },
  { name: "enhancement", color: "a2eeef", description: "New feature" },
  { name: "new-label", color: "ffffff", description: "Just added" },
  { name: "drift-color", color: "123456", description: "Color changed" },
  { name: "drift-desc", color: "abcdef", description: "Description changed" },
];

let readFileSyncCalls = [];
const mockFs = {
  readFileSync(path, encoding) {
    readFileSyncCalls.push({ path, encoding });
    return "mock yaml content";
  },
};

const mockYaml = {
  load(content) {
    assert.strictEqual(content, "mock yaml content");
    return mockManifest;
  },
};

// Inject mocks by intercepting Module._load before requiring labels-sync.
// This avoids needing js-yaml installed at test time.
const Module = require("module");
const originalLoad = Module._load;

Module._load = function (request, parent, isMain) {
  if (request === "fs") return mockFs;
  if (request === "js-yaml") return mockYaml;
  return originalLoad(request, parent, isMain);
};

// Now require labels-sync - it should pick up our mocks via interception
const { sync } = require("./labels-sync");

// Restore original loader after require
Module._load = originalLoad;

// ---------------------------------------------------------------------------
// sync
// ---------------------------------------------------------------------------
describe("sync", () => {
  it("creates missing labels, updates drifted ones, skips unchanged", async () => {
    readFileSyncCalls = [];
    const core = { info() {}, notice() {} };
    const context = { repo: { owner: "test-org", repo: "test-repo" } };

    const existingLabels = [
      { name: "bug", color: "d73a4a", description: "Something is broken" },
      { name: "enhancement", color: "a2eeef", description: "New feature" },
      { name: "drift-color", color: "654321", description: "Color changed" },
      { name: "drift-desc", color: "abcdef", description: "Old description" },
      { name: "unrelated", color: "000000", description: "Leave me alone" },
    ];

    const createdLabels = [];
    const updatedLabels = [];

    const github = {
      paginate: {
        async* iterator(listLabelsForRepo, params) {
          assert.strictEqual(
            listLabelsForRepo.name || listLabelsForRepo,
            github.rest.issues.listLabelsForRepo.name || github.rest.issues.listLabelsForRepo,
          );
          assert.deepStrictEqual(params, {
            owner: "test-org",
            repo: "test-repo",
            per_page: 100,
          });
          yield { data: existingLabels };
        },
      },
      rest: {
        issues: {
          listLabelsForRepo: "listLabelsForRepo",
          async createLabel(args) {
            createdLabels.push(args);
          },
          async updateLabel(args) {
            updatedLabels.push(args);
          },
        },
      },
    };

    const result = await sync({
      github,
      context,
      core,
      dryRun: false,
      manifestPath: ".github/labels.yml",
    });

    assert.strictEqual(readFileSyncCalls.length, 1);
    assert.strictEqual(readFileSyncCalls[0].path, ".github/labels.yml");
    assert.strictEqual(readFileSyncCalls[0].encoding, "utf8");

    assert.strictEqual(result.created, 1);
    assert.strictEqual(result.updated, 2);
    assert.strictEqual(result.skipped, 2); // bug, enhancement

    assert.strictEqual(createdLabels.length, 1);
    assert.deepStrictEqual(createdLabels[0], {
      owner: "test-org",
      repo: "test-repo",
      name: "new-label",
      color: "ffffff",
      description: "Just added",
    });

    assert.strictEqual(updatedLabels.length, 2);

    // Color drift
    const colorUpdate = updatedLabels.find((u) => u.name === "drift-color");
    assert.ok(colorUpdate);
    assert.strictEqual(colorUpdate.color, "123456");

    // Description drift
    const descUpdate = updatedLabels.find((u) => u.name === "drift-desc");
    assert.ok(descUpdate);
    assert.strictEqual(descUpdate.description, "Description changed");
  });

  it("dry run does not call create or update", async () => {
    readFileSyncCalls = [];
    const core = {
      logs: [],
      info(msg) {
        this.logs.push(msg);
      },
      notice(msg) {
        this.logs.push(msg);
      },
    };
    const context = { repo: { owner: "o", repo: "r" } };

    const github = {
      paginate: {
        async* iterator() {
          yield { data: [] };
        },
      },
      rest: {
        issues: {
          listLabelsForRepo: "listLabelsForRepo",
          async createLabel() {
            throw new Error("should not be called in dry run");
          },
          async updateLabel() {
            throw new Error("should not be called in dry run");
          },
        },
      },
    };

    const result = await sync({
      github,
      context,
      core,
      dryRun: true,
      manifestPath: ".github/labels.yml",
    });

    assert.strictEqual(result.created, 5);
    assert.strictEqual(result.updated, 0);
    assert.strictEqual(result.skipped, 0);

    // All 5 creates should be logged as CREATE but not executed
    const createLogs = core.logs.filter((l) => l.startsWith("CREATE"));
    assert.strictEqual(createLogs.length, 5);
  });

  it("skips when color and description match (case-insensitive color)", async () => {
    readFileSyncCalls = [];
    const core = { info() {}, notice() {} };
    const context = { repo: { owner: "o", repo: "r" } };

    const createdLabels = [];
    const updatedLabels = [];

    const github = {
      paginate: {
        async* iterator() {
          yield {
            data: [
              {
                name: "bug",
                color: "D73A4A",
                description: "Something is broken",
              },
            ],
          };
        },
      },
      rest: {
        issues: {
          listLabelsForRepo: "listLabelsForRepo",
          async createLabel(args) {
            createdLabels.push(args);
          },
          async updateLabel(args) {
            updatedLabels.push(args);
          },
        },
      },
    };

    const result = await sync({
      github,
      context,
      core,
      dryRun: false,
    });

    assert.strictEqual(result.created, 4); // enhancement, new-label, drift-color, drift-desc
    assert.strictEqual(result.updated, 0);
    assert.strictEqual(result.skipped, 1); // bug skipped due to case-insensitive match

    // Verify bug was NOT in created or updated
    assert.ok(
      !createdLabels.some((l) => l.name === "bug"),
      "bug should not have been created",
    );
    assert.ok(
      !updatedLabels.some((l) => l.name === "bug"),
      "bug should not have been updated",
    );
  });

  it("uses default manifest path when not provided", async () => {
    readFileSyncCalls = [];
    const core = { info() {}, notice() {} };
    const context = { repo: { owner: "o", repo: "r" } };

    const github = {
      paginate: {
        async* iterator() {
          yield { data: [] };
        },
      },
      rest: {
        issues: {
          listLabelsForRepo: "listLabelsForRepo",
          async createLabel() {},
          async updateLabel() {},
        },
      },
    };

    await sync({ github, context, core, dryRun: false });
    assert.strictEqual(readFileSyncCalls[0].path, ".github/labels.yml");
  });
});