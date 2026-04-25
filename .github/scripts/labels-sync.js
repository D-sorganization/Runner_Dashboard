// Shared label-sync logic. Invoked by labels-sync.yml and
// taxonomy-rollout.yml via actions/github-script.
//
// Reconciles .github/labels.yml with the repo's labels:
//   - creates labels listed in the manifest that don't exist yet
//   - updates color/description when they drift
//   - leaves unrelated existing labels alone
//
// Returns counters for the invoking workflow to log.

const fs = require("fs");
const yaml = require("js-yaml");

async function sync({ github, context, core, dryRun, manifestPath }) {
  const path = manifestPath || ".github/labels.yml";
  const manifest = yaml.load(fs.readFileSync(path, "utf8"));
  const { owner, repo } = context.repo;

  const existing = new Map();
  for await (const res of github.paginate.iterator(
    github.rest.issues.listLabelsForRepo,
    { owner, repo, per_page: 100 },
  )) {
    for (const l of res.data) existing.set(l.name, l);
  }

  let created = 0, updated = 0, skipped = 0;

  for (const entry of manifest) {
    const cur = existing.get(entry.name);
    if (!cur) {
      core.info(`CREATE ${entry.name}`);
      if (!dryRun) {
        await github.rest.issues.createLabel({
          owner, repo,
          name: entry.name,
          color: entry.color,
          description: entry.description || "",
        });
      }
      created++;
    } else if (
      cur.color.toLowerCase() !== entry.color.toLowerCase() ||
      (cur.description || "") !== (entry.description || "")
    ) {
      core.info(`UPDATE ${entry.name}`);
      if (!dryRun) {
        await github.rest.issues.updateLabel({
          owner, repo,
          name: entry.name,
          color: entry.color,
          description: entry.description || "",
        });
      }
      updated++;
    } else {
      skipped++;
    }
  }

  core.notice(
    `labels-sync: created=${created} updated=${updated} skipped=${skipped} dry_run=${dryRun}`,
  );
  return { created, updated, skipped };
}

module.exports = { sync };
