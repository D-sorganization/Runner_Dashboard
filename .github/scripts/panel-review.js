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

/**
 * Parse an opinion comment body into structured data.
 * Handles malformed opinions gracefully by returning null.
 *
 * @param {string} body - Comment body
 * @returns {object|null} Parsed attributes or null if not an opinion
 */
function parseOpinion(body) {
  if (!body) return null;
  const m = body.match(OPINION_MARKER_RE);
  if (!m) return null;

  const attrs = {};
  for (const pair of m[1].split(/\s+/)) {
    const eq = pair.indexOf("=");
    if (eq > 0) attrs[pair.slice(0, eq)] = pair.slice(eq + 1);
  }

  // Extract opinion sections: Opinion, Suggested approach and Risks
  const opinionMatch = body.match(
    /##\s+Opinion\s*\n([\s\S]*?)(?=##\s+(Suggested approach|Risks)|$)/i,
  );
  const approachMatch = body.match(
    /##\s+Suggested approach\s*\n([\s\S]*?)(?=##\s+Risks|$)/i,
  );
  const risksMatch = body.match(/##\s+Risks\s*\n([\s\S]*?)$/i);

  attrs.opinion = opinionMatch ? opinionMatch[1].trim() : null;
  attrs.approach = approachMatch ? approachMatch[1].trim() : null;
  attrs.risks = risksMatch ? risksMatch[1].trim() : null;

  return attrs;
}

/**
 * Calculate consensus level from tally and total opinions.
 *
 * @param {object} tally - Stance counts {support, oppose, modify, ...}
 * @param {number} totalOpinions - Total number of opinions
 * @returns {object} Consensus metadata {level, percent, label, dominantStance}
 */
function calculateConsensus(tally, totalOpinions) {
  if (totalOpinions < MIN_OPINIONS_FOR_SUMMARY) {
    return {
      level: "insufficient",
      percent: 0,
      label: "Insufficient consensus",
      dominantStance: "undecided",
    };
  }

  const support = tally.support || 0;
  const oppose = tally.oppose || 0;
  const modify = tally.modify || 0;

  const max = Math.max(support, oppose, modify);
  const percent = totalOpinions > 0 ? Math.round((max / totalOpinions) * 100) : 0;

  let level;
  if (percent === 100) {
    level = "unanimous";
  } else if (percent > 75) {
    level = "strong";
  } else if (percent >= 50) {
    level = "moderate";
  } else {
    level = "weak";
  }

  let dominantStance = "undecided";
  if (support > oppose && support > modify) {
    dominantStance = "support";
  } else if (oppose > support && oppose > modify) {
    dominantStance = "oppose";
  } else if (modify > support && modify > oppose) {
    dominantStance = "modify";
  } else if (support > 0 && oppose > 0 && support === oppose) {
    dominantStance = "contested";
  } else if (max > 0) {
    // Tie resolution: prefer support > oppose > modify
    if (support === max) dominantStance = "support";
    else if (oppose === max) dominantStance = "oppose";
    else dominantStance = "modify";
  }

  let label;
  if (support === 0 && oppose === 0 && modify === 0) {
    label = "No consensus";
  } else if (level === "unanimous") {
    label = "Unanimous consensus";
  } else if (level === "insufficient") {
    label = "Insufficient consensus";
  } else {
    label = `${level.charAt(0).toUpperCase() + level.slice(1)} consensus (${percent}%)`;
  }

  return { level, percent, label, dominantStance };
}

/**
 * Generate a structured summary markdown from panel opinions.
 *
 * @param {object} options
 * @param {Array} options.opinions - Array of {parsed, author} objects
 * @returns {string} Summary markdown
 */
function generateSummary({ opinions }) {
  const tally = opinions.reduce((acc, o) => {
    const s = (o.parsed.stance || "unknown").toLowerCase();
    acc[s] = (acc[s] || 0) + 1;
    return acc;
  }, {});

  // Group and rank approaches by number of supporters
  const approachGroups = {};
  for (const o of opinions) {
    if (o.parsed.approach) {
      const key = o.parsed.approach.trim();
      if (!approachGroups[key]) approachGroups[key] = [];
      approachGroups[key].push(o.author || "unknown");
    }
  }

  const rankedApproaches = Object.entries(approachGroups)
    .sort((a, b) => b[1].length - a[1].length)
    .map(([approach, authors]) => ({ approach, authors }));

  // Compile risks with counts (deduplicate by exact text)
  const riskMap = new Map();
  for (const o of opinions) {
    if (o.parsed.risks) {
      const key = o.parsed.risks.trim();
      const existing = riskMap.get(key) || [];
      existing.push(o.author || "unknown");
      riskMap.set(key, existing);
    }
  }

  const compiledRisks = Array.from(riskMap.entries()).map(([text, authors]) => ({
    text,
    count: authors.length,
    authors,
  }));

  const totalOpinions = opinions.length;
  const consensus = calculateConsensus(tally, totalOpinions);
  const timestamp = new Date().toISOString();

  const lines = [
    `<!-- panel-summary:v1 dominant_stance=${consensus.dominantStance} consensus=${(consensus.percent / 100).toFixed(2)} -->`,
    `## Panel Review Summary`,
    "",
    `**Dominant Stance:** ${consensus.dominantStance.toUpperCase()} (${consensus.label})`,
    "",
    "### Tally",
    `- Support: ${tally.support || 0} agent${(tally.support || 0) !== 1 ? "s" : ""}`,
    `- Oppose: ${tally.oppose || 0} agent${(tally.oppose || 0) !== 1 ? "s" : ""}`,
    `- Modify: ${tally.modify || 0} agent${(tally.modify || 0) !== 1 ? "s" : ""}`,
    "",
    "### Suggested Approaches",
  ];

  if (rankedApproaches.length > 0) {
    for (let i = 0; i < rankedApproaches.length; i++) {
      const { approach, authors } = rankedApproaches[i];
      const label = i === 0 ? "Recommended" : "Alternative";
      lines.push(`${i + 1}. **${label}:** ${approach}`);
      lines.push(`   - Supported by: ${authors.join(", ")}`);
    }
  } else {
    lines.push("_No approaches suggested._");
  }

  lines.push("");
  lines.push("### Risk Consensus");

  if (compiledRisks.length > 0) {
    for (const risk of compiledRisks) {
      lines.push(`- ${risk.text}`);
      lines.push(
        `  - Identified by ${risk.count} panelist${risk.count !== 1 ? "s" : ""}${risk.count < totalOpinions ? ` (${risk.authors.join(", ")})` : ""}`,
      );
    }
  } else {
    lines.push("_No risks identified._");
  }

  lines.push("");
  lines.push("---");
  lines.push("");
  lines.push(
    "**Next Step:** Maintainer review and label adjustment. Once consensus is clear, relabel to `judgement:objective` to unblock implementation.",
  );
  lines.push("");
  const panelistList = opinions.map((o) => `@${o.author || "unknown"}`).join(", ");
  lines.push(
    `*Panel review completed: ${timestamp} — Panelists responded: ${panelistList}*`,
  );

  return lines.join("\n");
}

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

async function summarize({ github, context, core, issue }) {
  const { owner, repo } = context.repo;
  const comments = await github.paginate(
    github.rest.issues.listComments,
    { owner, repo, issue_number: issue.number, per_page: 100 },
  );

  // Collect opinions, deduplicating by author (most recent wins)
  const opinionsByAuthor = new Map();
  for (const c of comments) {
    const parsed = parseOpinion(c.body);
    if (!parsed) continue;

    const author = c.user && c.user.login ? c.user.login : "unknown";
    // Overwrite with most recent opinion from this author
    opinionsByAuthor.set(author, { parsed, author });
  }

  const opinions = Array.from(opinionsByAuthor.values());

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

  const summaryBody = generateSummary({ opinions });

  await github.rest.issues.createComment({
    owner, repo, issue_number: issue.number, body: summaryBody,
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

module.exports = {
  postBrief,
  summarize,
  sweep,
  parseOpinion,
  calculateConsensus,
  generateSummary,
  AGENT_ROSTER,
  selectTiers,
  getAgentsForTiers,
};