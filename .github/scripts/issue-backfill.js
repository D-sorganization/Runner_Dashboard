// Shared issue-taxonomy-backfill logic. Invoked by
// issue-taxonomy-backfill.yml and taxonomy-rollout.yml via
// actions/github-script.
//
// Two independent operations:
//   - applyLabels(): adds the taxonomy labels listed in the manifest
//   - applyHierarchy(): adds native sub-issue edges via GraphQL
//
// Both idempotent. When collectPreview is set, each operation returns an
// array of human-readable preview lines so the caller can post a single
// diff comment to the migration epic.

const fs = require("fs");
const yaml = require("js-yaml");

const MANIFEST_PATH_DEFAULT = "docs/issue-taxonomy-backfill.yml";

function loadManifest(manifestPath) {
  const path = manifestPath || MANIFEST_PATH_DEFAULT;
  const manifest = yaml.load(fs.readFileSync(path, "utf8"));
  if (manifest.version !== 1) {
    throw new Error(`Unsupported manifest version: ${manifest.version}`);
  }
  return manifest;
}

async function applyLabels({ github, context, core, dryRun, manifestPath, collectPreview }) {
  const manifest = loadManifest(manifestPath);
  const { owner, repo } = context.repo;
  const preview = [];
  let added = 0, already = 0, missing = 0;

  for (const entry of manifest.issues) {
    const n = entry.number;
    const adds = entry.add || [];
    if (adds.length === 0) continue;

    let cur;
    try {
      const { data } = await github.rest.issues.get({
        owner, repo, issue_number: n,
      });
      cur = data;
    } catch (e) {
      core.warning(`#${n}: ${e.message}`);
      missing++;
      continue;
    }
    if (cur.state !== "open") {
      core.info(`#${n}: skipping (state=${cur.state})`);
      continue;
    }

    const existing = new Set(cur.labels.map((l) => l.name || l));
    const toAdd = adds.filter((l) => !existing.has(l));
    if (toAdd.length === 0) {
      already++;
      continue;
    }

    const line = `#${n}: ${dryRun ? "WOULD ADD" : "ADD"} ${toAdd.join(", ")}`;
    core.info(line);
    if (collectPreview) preview.push(line);

    if (!dryRun) {
      try {
        await github.rest.issues.addLabels({
          owner, repo, issue_number: n, labels: toAdd,
        });
        added++;
      } catch (e) {
        core.warning(`#${n}: addLabels failed (${e.message}). Did Labels Sync run?`);
        missing++;
      }
    } else {
      added++;
    }
  }

  core.notice(`labels: added=${added} already=${already} missing=${missing} dry_run=${dryRun}`);
  return { added, already, missing, preview };
}

async function resolveIssueNodeId(github, owner, repo, num) {
  const q = `
    query($owner:String!, $repo:String!, $n:Int!) {
      repository(owner:$owner, name:$repo) { issue(number:$n) { id } }
    }
  `;
  const res = await github.graphql(q, { owner, repo, n: num });
  return res.repository.issue && res.repository.issue.id;
}

async function applyHierarchy({ github, context, core, dryRun, manifestPath, collectPreview }) {
  const manifest = loadManifest(manifestPath);
  const { owner, repo } = context.repo;
  const preview = [];
  let added = 0, already = 0, skipped = 0;

  for (const [parentStr, spec] of Object.entries(manifest.parents || {})) {
    const parentNum = Number(parentStr);
    const parentId = await resolveIssueNodeId(github, owner, repo, parentNum);
    if (!parentId) {
      core.warning(`parent #${parentNum} not found; skipping`);
      continue;
    }

    // Existing sub-issues on this parent.
    const existingQ = `
      query($id:ID!) {
        node(id:$id) { ... on Issue { subIssues(first:100) { nodes { number } } } }
      }
    `;
    let existingNums = new Set();
    try {
      const existingRes = await github.graphql(existingQ, { id: parentId });
      existingNums = new Set(
        (existingRes.node && existingRes.node.subIssues &&
          existingRes.node.subIssues.nodes || []).map((x) => x.number),
      );
    } catch (e) {
      core.warning(`subIssues query on #${parentNum} failed: ${e.message}`);
    }

    for (const childNum of spec.children || []) {
      if (existingNums.has(childNum)) {
        already++;
        continue;
      }
      const childId = await resolveIssueNodeId(github, owner, repo, childNum);
      if (!childId) {
        core.warning(`child #${childNum} not found; skipping`);
        skipped++;
        continue;
      }

      const line = `#${parentNum} -> #${childNum}: ${dryRun ? "WOULD LINK" : "LINK"}`;
      core.info(line);
      if (collectPreview) preview.push(line);

      if (!dryRun) {
        const mut = `
          mutation($parentId:ID!, $childId:ID!) {
            addSubIssue(input:{issueId:$parentId, subIssueId:$childId}) { issue { number } }
          }
        `;
        try {
          await github.graphql(mut, { parentId, childId });
          added++;
        } catch (e) {
          core.warning(`link #${parentNum} -> #${childNum} failed: ${e.message}`);
          skipped++;
        }
      } else {
        added++;
      }
    }
  }

  core.notice(`hierarchy: added=${added} already=${already} skipped=${skipped} dry_run=${dryRun}`);
  return { added, already, skipped, preview };
}

module.exports = { applyLabels, applyHierarchy, loadManifest };
