// Tests for panel-review.js
// Run with: node --test .github/scripts/panel-review.test.js
const { describe, it } = require("node:test");
const assert = require("node:assert");
const {
  parseOpinion,
  calculateConsensus,
  generateSummary,
  selectTiers,
  getAgentsForTiers,
  AGENT_ROSTER,
} = require("./panel-review");

// ---------------------------------------------------------------------------
// parseOpinion
// ---------------------------------------------------------------------------
describe("parseOpinion", () => {
  it("parses a well-formed opinion comment", () => {
    const body = `<!-- panel-opinion:v1 agent=tier-1 stance=support -->
## Opinion
This looks good.

## Suggested approach
Use a standalone script.

## Risks
Minimal risk.`;

    const result = parseOpinion(body);
    assert.strictEqual(result.agent, "tier-1");
    assert.strictEqual(result.stance, "support");
    assert.strictEqual(result.opinion, "This looks good.");
    assert.strictEqual(result.approach, "Use a standalone script.");
    assert.strictEqual(result.risks, "Minimal risk.");
  });

  it("returns null for non-opinion comments", () => {
    assert.strictEqual(parseOpinion("Just a regular comment"), null);
    assert.strictEqual(parseOpinion(null), null);
    assert.strictEqual(parseOpinion(""), null);
  });

  it("parses opinion without optional sections", () => {
    const body = `<!-- panel-opinion:v1 agent=tier-2 stance=oppose -->
## Opinion
I disagree with this approach.`;

    const result = parseOpinion(body);
    assert.strictEqual(result.agent, "tier-2");
    assert.strictEqual(result.stance, "oppose");
    assert.strictEqual(result.opinion, "I disagree with this approach.");
    assert.strictEqual(result.approach, null);
    assert.strictEqual(result.risks, null);
  });

  it("handles malformed opinion gracefully", () => {
    const body = `<!-- panel-opinion:v1 agent=tier-1 -->
## Opinion
Missing stance attribute.`;

    const result = parseOpinion(body);
    assert.strictEqual(result.agent, "tier-1");
    assert.strictEqual(result.stance, undefined);
  });

  it("handles opinion with modify stance", () => {
    const body = `<!-- panel-opinion:v1 agent=tier-3 stance=modify -->
## Opinion
Needs changes.

## Suggested approach
Refactor the API.

## Risks
Breaking change.`;

    const result = parseOpinion(body);
    assert.strictEqual(result.stance, "modify");
    assert.strictEqual(result.approach, "Refactor the API.");
    assert.strictEqual(result.risks, "Breaking change.");
  });
});

// ---------------------------------------------------------------------------
// calculateConsensus
// ---------------------------------------------------------------------------
describe("calculateConsensus", () => {
  it("returns insufficient for fewer than 2 opinions", () => {
    const result = calculateConsensus({ support: 1 }, 1);
    assert.strictEqual(result.level, "insufficient");
    assert.strictEqual(result.dominantStance, "undecided");
  });

  it("calculates strong consensus (>75%)", () => {
    const result = calculateConsensus({ support: 4, oppose: 1 }, 5);
    assert.strictEqual(result.level, "strong");
    assert.strictEqual(result.percent, 80);
    assert.strictEqual(result.dominantStance, "support");
  });

  it("calculates unanimous consensus (100%)", () => {
    const result = calculateConsensus({ support: 4 }, 4);
    assert.strictEqual(result.level, "unanimous");
    assert.strictEqual(result.percent, 100);
    assert.strictEqual(result.dominantStance, "support");
  });

  it("calculates moderate consensus (50-75%)", () => {
    const result = calculateConsensus({ support: 2, oppose: 1, modify: 1 }, 4);
    assert.strictEqual(result.level, "moderate");
    assert.strictEqual(result.dominantStance, "support");
  });

  it("calculates weak consensus (<50%)", () => {
    const result = calculateConsensus({ support: 1, oppose: 0, modify: 2 }, 5);
    assert.strictEqual(result.level, "weak");
    assert.strictEqual(result.percent, 40);
    assert.strictEqual(result.dominantStance, "modify");
  });

  it("flags contested when support equals oppose", () => {
    const result = calculateConsensus({ support: 2, oppose: 2 }, 4);
    assert.strictEqual(result.dominantStance, "contested");
  });

  it("handles all zero counts", () => {
    const result = calculateConsensus({}, 2);
    assert.strictEqual(result.dominantStance, "undecided");
    assert.strictEqual(result.label, "No consensus");
  });

  it("resolves ties with priority support > oppose > modify", () => {
    // support=2, modify=2, oppose=0: tied max, support !== oppose
    // tie resolution should pick support (priority: support > oppose > modify)
    const result = calculateConsensus({ support: 2, oppose: 0, modify: 2 }, 4);
    assert.strictEqual(result.dominantStance, "support");
  });
});

// ---------------------------------------------------------------------------
// generateSummary
// ---------------------------------------------------------------------------
describe("generateSummary", () => {
  it("generates a complete summary with all sections", () => {
    const opinions = [
      {
        parsed: {
          agent: "tier-1",
          stance: "support",
          opinion: "Looks good",
          approach: "Use a standalone script",
          risks: "Minimal risk",
        },
        author: "claude",
      },
      {
        parsed: {
          agent: "tier-2",
          stance: "support",
          opinion: "Agreed",
          approach: "Use a standalone script",
          risks: "Needs tests",
        },
        author: "codex",
      },
      {
        parsed: {
          agent: "tier-3",
          stance: "modify",
          opinion: "Needs changes",
          approach: "Inline logic instead",
          risks: "Minimal risk",
        },
        author: "jules",
      },
    ];

    const summary = generateSummary({ opinions });
    assert.ok(summary.includes("<!-- panel-summary:v1"));
    assert.ok(summary.includes("dominant_stance=support"));
    assert.ok(summary.includes("consensus=0.67"));
    assert.ok(summary.includes("## Panel Review Summary"));
    assert.ok(summary.includes("### Tally"));
    assert.ok(summary.includes("Support: 2 agents"));
    assert.ok(summary.includes("Oppose: 0 agents"));
    assert.ok(summary.includes("Modify: 1 agent"));
    assert.ok(summary.includes("### Suggested Approaches"));
    assert.ok(summary.includes("**Recommended:** Use a standalone script"));
    assert.ok(summary.includes("**Alternative:** Inline logic instead"));
    assert.ok(summary.includes("### Risk Consensus"));
    assert.ok(summary.includes("Identified by 2 panelists"));
    assert.ok(summary.includes("@claude, @codex, @jules"));
    assert.ok(summary.includes("Panel review completed:"));
  });

  it("handles no approaches or risks gracefully", () => {
    const opinions = [
      {
        parsed: { agent: "tier-1", stance: "oppose" },
        author: "claude",
      },
      {
        parsed: { agent: "tier-2", stance: "oppose" },
        author: "codex",
      },
    ];

    const summary = generateSummary({ opinions });
    assert.ok(summary.includes("_No approaches suggested._"));
    assert.ok(summary.includes("_No risks identified._"));
  });

  it("handles single opinion with correct pluralization", () => {
    const opinions = [
      {
        parsed: {
          agent: "tier-1",
          stance: "support",
          approach: "Do it",
        },
        author: "claude",
      },
    ];

    const summary = generateSummary({ opinions });
    assert.ok(summary.includes("Support: 1 agent"));
    assert.ok(summary.includes("Oppose: 0 agents"));
    assert.ok(!summary.includes("1 agents"));
  });

  it("includes panelist names in summary", () => {
    const opinions = [
      { parsed: { stance: "support" }, author: "agentA" },
      { parsed: { stance: "oppose" }, author: "agentB" },
    ];
    const summary = generateSummary({ opinions });
    assert.ok(summary.includes("@agentA"));
    assert.ok(summary.includes("@agentB"));
  });

  it("uses risk count singular when one panelist identifies", () => {
    const opinions = [
      {
        parsed: { stance: "support", risks: "Security concern" },
        author: "agentA",
      },
      {
        parsed: { stance: "support" },
        author: "agentB",
      },
    ];
    const summary = generateSummary({ opinions });
    assert.ok(summary.includes("Identified by 1 panelist"));
  });
});

// ---------------------------------------------------------------------------
// selectTiers / getAgentsForTiers
// ---------------------------------------------------------------------------
describe("selectTiers", () => {
  it("selects tier-1 for trivial complexity", () => {
    assert.deepStrictEqual(selectTiers("trivial"), ["tier-1", "tier-2", "tier-3"]);
  });

  it("selects all tiers for routine complexity", () => {
    assert.deepStrictEqual(selectTiers("routine"), ["tier-1", "tier-2", "tier-3"]);
  });

  it("selects tier-2 and tier-3 for complex", () => {
    assert.deepStrictEqual(selectTiers("complex"), ["tier-2", "tier-3"]);
  });

  it("selects only tier-3 for deep", () => {
    assert.deepStrictEqual(selectTiers("deep"), ["tier-3"]);
  });

  it("selects research for research complexity", () => {
    assert.deepStrictEqual(selectTiers("research"), ["research"]);
  });

  it("returns empty array for unknown complexity", () => {
    assert.deepStrictEqual(selectTiers("unknown"), []);
  });
});

describe("getAgentsForTiers", () => {
  it("returns unique agents for given tiers", () => {
    const agents = getAgentsForTiers(["tier-1", "tier-2"]);
    assert.ok(agents.includes("claude"));
    assert.ok(agents.includes("codex"));
    assert.ok(agents.includes("maxwell-daemon"));
  });

  it("returns empty array for unknown tiers", () => {
    assert.deepStrictEqual(getAgentsForTiers(["nonexistent"]), []);
  });
});

describe("AGENT_ROSTER", () => {
  it("has expected structure", () => {
    assert.ok(AGENT_ROSTER["tier-1"]);
    assert.ok(AGENT_ROSTER["tier-2"]);
    assert.ok(AGENT_ROSTER["tier-3"]);
    assert.ok(AGENT_ROSTER.research);
  });
});