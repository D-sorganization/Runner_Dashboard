#!/usr/bin/env python3
"""Comprehensive A-O assessment for runner-dashboard."""

import json
import re
import subprocess
from pathlib import Path

REPO = "runner-dashboard"
OWNER = "D-sorganization"
DATE = "2026-04-26"


def run(cmd, cwd=REPO, check=False):
    try:
        return subprocess.check_output(cmd, cwd=cwd, stderr=subprocess.STDOUT, text=True)
    except subprocess.CalledProcessError as e:
        return e.stdout if check else ""
    except FileNotFoundError:
        return ""


def grep_py(pattern, directory):
    """Python-based grep for Windows compatibility."""
    matches = []
    base = Path(REPO) / directory
    if not base.exists():
        return []
    for f in base.rglob("*.py"):
        try:
            txt = f.read_text(encoding="utf-8", errors="ignore")
            for i, line in enumerate(txt.splitlines(), 1):
                if re.search(pattern, line):
                    matches.append((str(f.relative_to(REPO)), i, line.strip()))
        except Exception:
            pass
    return matches


def main():
    findings = []
    scores = {}

    # A. Project Organization & Structure
    score_a = 8
    gitignore = Path(REPO) / ".gitignore"
    if gitignore.exists():
        gi = gitignore.read_text()
        if "__pycache__" not in gi or ".venv" not in gi:
            findings.append(
                {
                    "criterion": "A",
                    "severity": "P2",
                    "principle": "PP8",
                    "text": ".gitignore missing __pycache__ or .venv coverage",
                }
            )
            score_a = max(score_a - 1, 0)
    else:
        findings.append({"criterion": "A", "severity": "P1", "principle": "PP8", "text": "No .gitignore present"})
        score_a = 3

    for junk in ["misc", "stuff", "old", "temp", "tmp"]:
        if (Path(REPO) / junk).exists():
            findings.append(
                {
                    "criterion": "A",
                    "severity": "P2",
                    "principle": "PP8",
                    "text": f"Junk drawer directory '{junk}' exists",
                }
            )
            score_a = max(score_a - 1, 0)

    pyproject = Path(REPO) / "pyproject.toml"
    req = Path(REPO) / "requirements.txt"
    if not pyproject.exists() and not req.exists():
        findings.append(
            {
                "criterion": "A",
                "severity": "P1",
                "principle": "PP2",
                "text": "No package manifest (pyproject.toml or requirements.txt)",
            }
        )
        score_a = max(score_a - 3, 0)

    scores["A"] = score_a

    # B. Documentation & Domain Language
    score_b = 7
    readme = Path(REPO) / "README.md"
    if readme.exists():
        lines = len(readme.read_text().splitlines())
        if lines < 30:
            findings.append(
                {
                    "criterion": "B",
                    "severity": "P2",
                    "principle": "PP4",
                    "text": f"README.md only {lines} lines (<30)",
                }
            )
            score_b = max(score_b - 2, 0)
    else:
        findings.append({"criterion": "B", "severity": "P0", "principle": "PP4", "text": "README.md missing"})
        score_b = 0

    if not (Path(REPO) / "CLAUDE.md").exists():
        findings.append(
            {
                "criterion": "B",
                "severity": "P2",
                "principle": "PP4",
                "text": "No CLAUDE.md or AGENTS.md for agent onboarding",
            }
        )
        score_b = max(score_b - 1, 0)

    backend_py = list((Path(REPO) / "backend").rglob("*.py")) if (Path(REPO) / "backend").exists() else []
    sampled = backend_py[:15]
    total_funcs = 0
    docstring_funcs = 0
    for f in sampled:
        try:
            txt = f.read_text()
            lines = txt.splitlines()
            for i, line in enumerate(lines):
                if line.strip().startswith("def ") and not line.strip().startswith("def _"):
                    total_funcs += 1
                    if i + 1 < len(lines) and ('"""' in lines[i + 1] or "'''" in lines[i + 1]):
                        docstring_funcs += 1
        except Exception:
            pass
    if total_funcs > 0 and docstring_funcs / total_funcs < 0.5:
        findings.append(
            {
                "criterion": "B",
                "severity": "P1",
                "principle": "PP4",
                "text": f"Docstring coverage low: {docstring_funcs}/{total_funcs} public functions in backend/ sample",
            }
        )
        score_b = max(score_b - 1, 0)

    adr_dir = Path(REPO) / "docs" / "adr"
    if not adr_dir.exists() or not list(adr_dir.glob("*.md")):
        findings.append({"criterion": "B", "severity": "P2", "principle": "PP4", "text": "No ADRs found in docs/adr/"})
        score_b = max(score_b - 1, 0)

    scores["B"] = score_b

    # C. Testing & Validation
    score_c = 5
    tests_dir = Path(REPO) / "tests"
    if tests_dir.exists():
        test_files = list(tests_dir.rglob("test_*.py"))
        if len(test_files) == 0:
            findings.append(
                {
                    "criterion": "C",
                    "severity": "P1",
                    "principle": "PP7",
                    "text": "tests/ exists but no test_*.py files found",
                }
            )
            score_c = max(score_c - 2, 0)
    else:
        findings.append({"criterion": "C", "severity": "P0", "principle": "PP7", "text": "No tests/ directory"})
        score_c = 0

    if not (Path(REPO) / ".coverage").exists():
        findings.append({"criterion": "C", "severity": "P2", "principle": "PP7", "text": "No .coverage file found"})

    skip_pat = re.compile(r"pytest\.(skip|xfail)\s*\(")
    for f in list(tests_dir.rglob("*.py"))[:50] if tests_dir.exists() else []:
        try:
            txt = f.read_text()
            for m in skip_pat.finditer(txt):
                ctx = txt[m.start() : m.start() + 200]
                if "issue" not in ctx.lower() and "#" not in ctx:
                    findings.append(
                        {
                            "criterion": "C",
                            "severity": "P2",
                            "principle": "PP8",
                            "text": f"pytest.skip/xfail without issue link in {f.name}",
                        }
                    )
        except Exception:
            pass

    scores["C"] = score_c

    # D. Robustness & Error Handling
    score_d = 6
    bare_except = grep_py(r"except\s*:\s*$|except\s+Exception\s*:\s*pass", "backend")
    if bare_except:
        findings.append(
            {"criterion": "D", "severity": "P1", "principle": "PP6", "text": "Bare except blocks found in backend/"}
        )
        score_d = max(score_d - 2, 0)

    for f in backend_py[:50]:
        try:
            txt = f.read_text()
            if re.search(r"try:.*except.*:.*\n\s*return\s+None", txt, re.DOTALL):
                findings.append(
                    {
                        "criterion": "D",
                        "severity": "P2",
                        "principle": "PP6",
                        "text": f"try/except returning None without logging in {f.name}",
                    }
                )
        except Exception:
            pass

    scores["D"] = score_d

    # E. Performance & Scalability
    score_e = 5
    for f in backend_py[:30]:
        try:
            txt = f.read_text()
            if re.search(r"for\s+\w+\s+in\s+.*:\s*\n.*open\(", txt):
                findings.append(
                    {
                        "criterion": "E",
                        "severity": "P2",
                        "principle": "PP4",
                        "text": f"Possible repeated file I/O inside loop in {f.name}",
                    }
                )
        except Exception:
            pass

    if not (Path(REPO) / "benchmarks").exists():
        findings.append({"criterion": "E", "severity": "P2", "principle": "PP4", "text": "No benchmarks/ directory"})

    scores["E"] = score_e

    # F. Code Craftsmanship
    score_f = 5
    for f in backend_py:
        try:
            lines = len(f.read_text().splitlines())
            if lines > 500:
                findings.append(
                    {
                        "criterion": "F",
                        "severity": "P0",
                        "principle": "PP1",
                        "text": f"God file {f.name} ({lines} lines) exceeds 500-line soft cap",
                    }
                )
                score_f = max(score_f - 2, 0)
        except Exception:
            pass

    magic = 0
    for f in backend_py[:30]:
        try:
            txt = f.read_text()
            magic += len(re.findall(r"(?<![\w\d_])\d+\.\d+(?![\w\d_])", txt))
        except Exception:
            pass
    if magic > 20:
        findings.append(
            {
                "criterion": "F",
                "severity": "P2",
                "principle": "PP1",
                "text": f"Many magic float literals ({magic}) in backend/ sample",
            }
        )

    scores["F"] = score_f

    # G. Dependencies & Supply Chain
    score_g = 6
    lockfiles = ["poetry.lock", "package-lock.json", "requirements.lock", "Pipfile.lock"]
    has_lock = any((Path(REPO) / lf).exists() for lf in lockfiles)
    if not has_lock:
        findings.append(
            {
                "criterion": "G",
                "severity": "P1",
                "principle": "PP3",
                "text": "No lockfile present (poetry.lock, package-lock.json, etc.)",
            }
        )
        score_g = max(score_g - 2, 0)

    audit = run(["pip-audit", "-r", "requirements.txt"], check=True)
    if "CVE" in audit:
        findings.append(
            {"criterion": "G", "severity": "P0", "principle": "PP3", "text": "pip-audit found CVEs in dependencies"}
        )
        score_g = max(score_g - 3, 0)

    scores["G"] = score_g

    # H. Security Posture
    score_h = 7
    bandit = run(["bandit", "-r", ".", "-ll", "-q"], check=True)
    if "Issue:" in bandit:
        findings.append(
            {
                "criterion": "H",
                "severity": "P0",
                "principle": "PP6",
                "text": "bandit found HIGH/ MEDIUM severity issues",
            }
        )
        score_h = max(score_h - 3, 0)

    cred = []
    for f in backend_py:
        try:
            txt = f.read_text()
            for i, line in enumerate(txt.splitlines(), 1):
                if re.search(r'(password|secret|api[_-]?key|token)\s*=\s*["\']', line, re.IGNORECASE):
                    cred.append((str(f), i))
        except Exception:
            pass
    if cred:
        findings.append(
            {
                "criterion": "H",
                "severity": "P0",
                "principle": "PP6",
                "text": "Possible hard-coded credentials in source",
            }
        )
        score_h = max(score_h - 3, 0)

    scores["H"] = score_h

    # I. Configuration & Environment Management
    score_i = 7
    if not (Path(REPO) / ".env.example").exists():
        findings.append(
            {
                "criterion": "I",
                "severity": "P2",
                "principle": "PP3",
                "text": "No .env.example documenting required variables",
            }
        )
        score_i = max(score_i - 1, 0)

    if not (Path(REPO) / "Dockerfile").exists():
        findings.append(
            {
                "criterion": "I",
                "severity": "P2",
                "principle": "PP3",
                "text": "No Dockerfile for reproducible dev environment",
            }
        )

    scores["I"] = score_i

    # J. Logging, Observability & Telemetry
    score_j = 5
    prints = grep_py(r"^\s*print\(", "backend")
    if prints:
        findings.append(
            {
                "criterion": "J",
                "severity": "P2",
                "principle": "PP6",
                "text": "print() statements found in backend/ (use logging)",
            }
        )
        score_j = max(score_j - 2, 0)

    scores["J"] = score_j

    # K. Maintainability & Tech Debt
    score_k = 4
    todo_count = 0
    for d in ["backend", "tests", "frontend"]:
        base = Path(REPO) / d
        if base.exists():
            for f in base.rglob("*.py"):
                try:
                    txt = f.read_text()
                    for line in txt.splitlines():
                        if re.search(r"TODO|FIXME|XXX|HACK|KLUDGE", line):
                            todo_count += 1
                except Exception:
                    pass
    if todo_count > 20:
        findings.append(
            {
                "criterion": "K",
                "severity": "P0",
                "principle": "PP8",
                "text": f"{todo_count} TODO/FIXME/XXX comments without issue links",
            }
        )
        score_k = max(score_k - 3, 0)
    elif todo_count > 5:
        findings.append(
            {
                "criterion": "K",
                "severity": "P1",
                "principle": "PP8",
                "text": f"{todo_count} TODO/FIXME/XXX comments -- link to issues",
            }
        )
        score_k = max(score_k - 1, 0)

    scores["K"] = score_k

    # L. CI/CD & Automation
    score_l = 4
    workflows = Path(REPO) / ".github" / "workflows"
    if workflows.exists():
        wfs = list(workflows.glob("*.yml")) + list(workflows.glob("*.yaml"))
        if len(wfs) == 0:
            findings.append(
                {
                    "criterion": "L",
                    "severity": "P1",
                    "principle": "PP7",
                    "text": ".github/workflows/ exists but no workflow files",
                }
            )
            score_l = max(score_l - 3, 0)
    else:
        findings.append(
            {"criterion": "L", "severity": "P0", "principle": "PP7", "text": "No .github/workflows/ directory"}
        )
        score_l = 0

    if not (Path(REPO) / ".pre-commit-config.yaml").exists():
        findings.append({"criterion": "L", "severity": "P2", "principle": "PP7", "text": "No .pre-commit-config.yaml"})

    scores["L"] = score_l

    # M. Deployment & Operability
    score_m = 6
    deploy_dir = Path(REPO) / "deploy"
    if deploy_dir.exists():
        if not any((deploy_dir / f).exists() for f in ["rollback.sh", "README.md", "runbook.md"]):
            findings.append(
                {
                    "criterion": "M",
                    "severity": "P2",
                    "principle": "PP3",
                    "text": "deploy/ exists but no rollback.sh or runbook",
                }
            )
            score_m = max(score_m - 1, 0)
    else:
        findings.append({"criterion": "M", "severity": "P2", "principle": "PP3", "text": "No deploy/ directory"})
        score_m = max(score_m - 1, 0)

    if not (Path(REPO) / "VERSION").exists():
        findings.append({"criterion": "M", "severity": "P2", "principle": "PP5", "text": "No VERSION file"})

    scores["M"] = score_m

    # N. Compliance, Licensing & Governance
    score_n = 8
    if not (Path(REPO) / "LICENSE").exists():
        findings.append({"criterion": "N", "severity": "P1", "principle": "PP3", "text": "LICENSE file missing"})
        score_n = max(score_n - 3, 0)

    if not (Path(REPO) / "SECURITY.md").exists():
        findings.append({"criterion": "N", "severity": "P2", "principle": "PP3", "text": "SECURITY.md missing"})
        score_n = max(score_n - 1, 0)

    if not (Path(REPO) / "CONTRIBUTING.md").exists():
        findings.append({"criterion": "N", "severity": "P2", "principle": "PP3", "text": "CONTRIBUTING.md missing"})
        score_n = max(score_n - 1, 0)

    scores["N"] = score_n

    # O. Agentic Usability
    score_o = 7
    if not (Path(REPO) / "CLAUDE.md").exists() and not (Path(REPO) / "AGENTS.md").exists():
        findings.append(
            {
                "criterion": "O",
                "severity": "P1",
                "principle": "PP4",
                "text": "No agent onboarding doc (CLAUDE.md or AGENTS.md)",
            }
        )
        score_o = max(score_o - 2, 0)

    if not (Path(REPO) / "SPEC.md").exists():
        findings.append({"criterion": "O", "severity": "P2", "principle": "PP4", "text": "SPEC.md missing"})
        score_o = max(score_o - 1, 0)

    scores["O"] = score_o

    overall = round(sum(scores.values()) / len(scores), 1)
    report = {
        "meta": {
            "repo": f"{OWNER}/{REPO}",
            "date": DATE,
            "head_short": run(["git", "log", "-1", "--format=%h"]).strip(),
            "head_long": run(["git", "log", "-1", "--format=%H"]).strip(),
            "branch": run(["git", "rev-parse", "--abbrev-ref", "HEAD"]).strip(),
            "assessor": "pragmatic-ao-assessment-agent",
        },
        "scores": scores,
        "overall": overall,
        "findings": findings,
    }

    out_dir = Path("assessments")
    out_dir.mkdir(exist_ok=True)
    (out_dir / f"{DATE}-{REPO}-assessment.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
