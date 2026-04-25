// Panel review helper, called from .github/workflows/agent-panel-review.yml.
//
// Responsibilities:
//
//   1. postBrief(issue)
//      Posts a single "dispatch brief" comment on an issue labeled
//      `panel-review`. The brief tells panelists what to weigh in on and
//      the comment format to use. Idempotent: if a brief already exists
//      on the issue, it is not re-posted.
//
//   2. sweep({ specificIssueNumber, mode })
//      Iterates all open issues labeled `panel-review` (or just the one
//      in specificIssueNumber) and:
//        - posts a brief if one is missing (mode = brief or both)
//        - tallies panel-opinion comments and posts a summary when enough
//          opinions exist (mode = summarize or both)
//
// The workflow does not invoke panelists. Panelists are agent workflows
// that subscribe to issue comments or labels and respond on their own
// cadence. This script only curates the conversation.

const BRIEF_MARKER = "<!-- panel-review:brief:v1 -->";
const SUMMARY_MARKER = "<!-- panel-review:summary:v1 -->";
const OPINION_MARKER_RE = /<!--\s*panel-opinion:v1\s+([^>]+?)\s*-->/;
const MIN_OPINIONS_FOR_SUMMARY = 2;

async function postBrief({ github, context, core, issue }) {
  const { owner, repo } = context.repo;
  const comments = await github.paginate(
    github.rest.issues.listComments,
    { owner, repo, issue_number: issue.number, per_page: 100 },
  );

  if (comments.some((c) => c.body && c.body.includes(BRIEF_MARKER))) {
    core.info(`Brief already exists on #${issue.number}`);
    return;
  }

  // Extract complexity label to suggest tier(s)
  const complexityLabel = issue.labels
    .map((l) => l.name)
    .find((name) => name.startsWith("complexity:"));
  const complexity = complexityLabel
    ? complexityLabel.replace("complexity:", "")
    : "unknown";
  const recommendedTiers = selectTiers(complexity);
  const recommendedAgents = getAgentsForTiers(recommendedTiers);

  const tierInfo =
    recommendedTiers.length > 0
      ? `**Complexity:** \`${complexity}\` → Recommended reviewers: ${recommendedAgents.map((a) => `\`${a}\``).join(", ")}`
      : "**Complexity:** unknown (no complexity label found)";

  const brief = [
    BRIEF_MARKER,
    "## Panel review requested",
    "",
    "This issue is flagged `panel-review`. Multiple agents are invited to",
    "weigh in **before** anyone opens a PR. Implementation is blocked until",
    "a human removes the `panel-review` label and sets a non-`design` /",
    "non-`contested` judgement label.",
    "",
    tierInfo,
    "",
    "### How to respond",
    "",
    "Post **one** comment in this format:",
    "",
    "````",
    "<!-- panel-opinion:v1 agent=<tier> stance=support|oppose|modify -->",
    "## Opinion",
    "<2-3 sentence summary>",
    "",
    "## Suggested approach",
    "<specific recommendation>",
    "",
    "## Risks",
    "<identified risks or concerns>",
    "````",
    "",
    "`agent` is your agent tier (`tier-1`, `tier-2`, `tier-3`, or `research`).",
    "`stance` is one of `support`, `oppose`, `modify`.",
    "",
    "See [docs/issue-taxonomy.md](../blob/main/docs/issue-taxonomy.md#panel-review)",
    "for the full contract.",
  ].join("\n");

  await github.rest.issues.createComment({
    owner, repo, issue_number: issue.number, body: brief,
  });
  core.info(
    `Posted brief on #${issue.number} (recommended tiers: ${recommendedTiers.join(", ")})`,
  );
}

function parseOpinion(body) {
  if (!body) return null;
  const m = body.match(OPINION_MARKER_RE);
  if (!m) return null;
  const attrs = {};
  for (const pair of m[1].split(/\s+/)) {
    const eq = pair.indexOf("=");
    if (eq > 0) attrs[pair.slice(0, eq)] = pair.slice(eq + 1);
  }

  // Extract opinion sections: Suggested approach and Risks
  const opinionMatch = body.match(
    /##\s+Suggested approach\s*\n([\s\S]*?)(?=##\s+Risks|$)/i,
  );
  const risksMatch = body.match(/##\s+Risks\s*\n([\s\S]*?)$/i);

  attrs.approach = opinionMatch ? opinionMatch[1].trim() : null;
  attrs.risks = risksMatch ? risksMatch[1].trim() : null;

  return attrs;
}

async function summarize({ github, context, core, issue }) {
  const { owner, repo } = context.repo;
  const comments = await github.paginate(
    github.rest.issues.listComments,
    { owner, repo, issue_number: issue.number, per_page: 100 },
  );

  const opinions = [];
  for (const c of comments) {
    const parsed = parseOpinion(c.body);
    if (parsed) opinions.push({ parsed, author: c.user && c.user.login });
  }

  if (opinions.length < MIN_OPINIONS_FOR_SUMMARY) {
    core.info(
      `#${issue.number} has ${opinions.length} opinions; need ${MIN_OPINIONS_FOR_SUMMARY}`,
    );
    return;
  }

  // Skip if we already posted a summary for this opinion count.
  const existingSummary = comments.find(
    (c) => c.body && c.body.includes(SUMMARY_MARKER),
  );
  if (existingSummary) {
    const tag = `(opinions=${opinions.length})`;
    if (existingSummary.body.includes(tag)) {
      core.info(`#${issue.number} already has summary for ${opinions.length} opinions`);
      return;
    }
  }

  const tally = opinions.reduce((acc, o) => {
    const s = (o.parsed.stance || "unknown").toLowerCase();
    acc[s] = (acc[s] || 0) + 1;
    return acc;
  }, {});

  // Aggregate approaches and risks
  const approaches = {};
  const risksList = [];

  for (const o of opinions) {
    if (o.parsed.approach) {
      const key = o.parsed.approach.substring(0, 80); // Group similar approaches
      approaches[key] = (approaches[key] || []).concat(o.author || "unknown");
    }
    if (o.parsed.risks) {
      risksList.push({
        author: o.author || "unknown",
        text: o.parsed.risks,
      });
    }
  }

  // Calculate consensus percentage
  const totalOpinions = opinions.length;
  const supportCount = tally.support || 0;
  const consensusPercent = totalOpinions > 0
    ? Math.round((supportCount / totalOpinions) * 100)
    : 0;

  const lines = [
    SUMMARY_MARKER,
    `## Panel summary (opinions=${opinions.length})`,
    "",
    "| Stance | Count |",
    "|---|---|",
    ...Object.entries(tally)
      .sort((a, b) => b[1] - a[1])
      .map(([stance, n]) => `| \`${stance}\` | ${n} |`),
    "",
    supportCount > 0 && totalOpinions > 1
      ? `**Consensus:** ${consensusPercent}% support`
      : supportCount === 1 ? "**Consensus:** Single support (pending more opinions)" : "**Consensus:** Undecided",
    "",
  ];

  // Add approaches if any were extracted
  if (Object.keys(approaches).length > 0) {
    lines.push("### Suggested Approaches");
    let approachNum = 1;
    for (const [approach, authors] of Object.entries(approaches)) {
      lines.push(`${approachNum}. ${approach}`);
      lines.push(`   - Suggested by: ${authors.join(", ")}`);
      approachNum++;
    }
    lines.push("");
  }

  // Add risks if any were extracted
  if (risksList.length > 0) {
    lines.push("### Identified Risks");
    for (const risk of risksList) {
      lines.push(`- **@${risk.author}**: ${risk.text}`);
    }
    lines.push("");
  }

  lines.push(
    "### Panelists",
    ...opinions.map(
      (o) =>
        `- @${o.author || "unknown"} — \`${o.parsed.agent || "?"}\`, stance \`${o.parsed.stance || "?"}\``,
    ),
    "",
    "Once the team has picked a direction, remove the `panel-review` label",
    "and set `judgement:objective` / `judgement:preference` to unblock",
    "implementation.",
  );

  await github.rest.issues.createComment({
    owner, repo, issue_number: issue.number, body: lines.join("\n"),
  });
  core.info(`Posted summary on #${issue.number}`);
}

async function sweep({ github, context, core, specificIssueNumber, mode }) {
  const { owner, repo } = context.repo;

  let issues;
  if (specificIssueNumber) {
    const { data } = await github.rest.issues.get({
      owner, repo, issue_number: specificIssueNumber,
    });
    issues = [data];
  } else {
    issues = await github.paginate(github.rest.issues.listForRepo, {
      owner, repo, state: "open", labels: "panel-review", per_page: 100,
    });
  }

  core.info(`Sweeping ${issues.length} panel-review issue(s) in mode=${mode}`);

  for (const issue of issues) {
    if (issue.pull_request) continue; // listForRepo mixes in PRs

    if (mode === "brief" || mode === "both") {
      await postBrief({ github, context, core, issue });
    }
    if (mode === "summarize" || mode === "both") {
      await summarize({ github, context, core, issue });
    }
  }
}

// Agent roster configuration for Phase 2 automated dispatch.
// Maps agent tier to available agents and their domains.
const AGENT_ROSTER = {
  "tier-1": {
    agents: ["claude", "codex"],
    complexity: ["trivial", "routine"],
    domains: ["backend", "frontend", "tests", "ci", "code-quality"],
  },
  "tier-2": {
    agents: ["claude", "maxwell-daemon", "codex"],
    complexity: ["complex"],
    domains: ["backend", "frontend", "architecture", "security", "agent-safety"],
  },
  "tier-3": {
    agents: ["claude", "maxwell-daemon", "jules"],
    complexity: ["deep"],
    domains: ["security", "architecture", "supply-chain", "governance"],
  },
  research: {
    agents: ["claude"],
    complexity: ["research"],
    domains: ["research", "spike", "feasibility"],
  },
};

// Determine which tier(s) should review based on issue complexity.
function selectTiers(issueComplexity) {
  const tiers = [];
  if (
    ["trivial", "routine"].includes(issueComplexity)
  ) tiers.push("tier-1");
  if (
    ["trivial", "routine", "complex"].includes(issueComplexity)
  ) tiers.push("tier-2");
  if (
    ["trivial", "routine", "complex", "deep"].includes(issueComplexity)
  ) tiers.push("tier-3");
  if (issueComplexity === "research") tiers.push("research");
  return [...new Set(tiers)];
}

// Get all unique agents for the selected tiers.
function getAgentsForTiers(tiers) {
  const agents = new Set();
  for (const tier of tiers) {
    if (AGENT_ROSTER[tier]) {
      AGENT_ROSTER[tier].agents.forEach((a) => agents.add(a));
    }
  }
  return Array.from(agents);
}

module.exports = { postBrief, summarize, sweep, AGENT_ROSTER, selectTiers, getAgentsForTiers };
