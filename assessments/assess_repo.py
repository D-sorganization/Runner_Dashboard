#!/usr/bin/env python3
"""Parameterized A-O assessment for any D-Sorganization repo."""

import sys, subprocess, json, re
from pathlib import Path

DATE = "2026-04-26"
OWNER = "D-sorganization"

def run(cmd, cwd, check=False):
    try:
        return subprocess.check_output(cmd, cwd=cwd, stderr=subprocess.STDOUT, text=True)
    except subprocess.CalledProcessError as e:
        return e.stdout if check else ""
    except FileNotFoundError:
        return ""

def grep_py(pattern, repo, directory):
    matches = []
    base = Path(repo) / directory
    if not base.exists():
        return []
    for f in base.rglob("*.py"):
        try:
            txt = f.read_text(encoding="utf-8", errors="ignore")
            for i, line in enumerate(txt.splitlines(), 1):
                if re.search(pattern, line):
                    matches.append((str(f.relative_to(repo)), i, line.strip()))
        except:
            pass
    return matches

def assess(repo):
    findings = []
    scores = {}
    backend_py = list((Path(repo) / "backend").rglob("*.py")) if (Path(repo) / "backend").exists() else []
    src_py = list((Path(repo) / "src").rglob("*.py")) if (Path(repo) / "src").exists() else []
    all_py = backend_py + src_py

    # A. Project Organization & Structure
    score_a = 8
    gitignore = Path(repo) / ".gitignore"
    if gitignore.exists():
        gi = gitignore.read_text(encoding="utf-8", errors="ignore")
        if "__pycache__" not in gi or ".venv" not in gi:
            findings.append({"criterion":"A","severity":"P2","principle":"PP8","text":".gitignore missing __pycache__ or .venv coverage"})
            score_a = max(score_a - 1, 0)
    else:
        findings.append({"criterion":"A","severity":"P1","principle":"PP8","text":"No .gitignore present"})
        score_a = 3
    for junk in ["misc", "stuff", "old", "temp", "tmp"]:
        if (Path(repo) / junk).exists():
            findings.append({"criterion":"A","severity":"P2","principle":"PP8","text":"Junk drawer directory '{}' exists".format(junk)})
            score_a = max(score_a - 1, 0)
    pyproject = Path(repo) / "pyproject.toml"
    req = Path(repo) / "requirements.txt"
    pkg = Path(repo) / "package.json"
    if not pyproject.exists() and not req.exists() and not pkg.exists():
        findings.append({"criterion":"A","severity":"P1","principle":"PP2","text":"No package manifest (pyproject.toml, requirements.txt, package.json)"})
        score_a = max(score_a - 3, 0)
    scores["A"] = score_a

    # B. Documentation & Domain Language
    score_b = 7
    readme = Path(repo) / "README.md"
    if readme.exists():
        lines = len(readme.read_text(encoding="utf-8", errors="ignore").splitlines())
        if lines < 30:
            findings.append({"criterion":"B","severity":"P2","principle":"PP4","text":"README.md only {} lines (<30)".format(lines)})
            score_b = max(score_b - 2, 0)
    else:
        findings.append({"criterion":"B","severity":"P0","principle":"PP4","text":"README.md missing"})
        score_b = 0
    if not (Path(repo) / "CLAUDE.md").exists() and not (Path(repo) / "AGENTS.md").exists():
        findings.append({"criterion":"B","severity":"P2","principle":"PP4","text":"No CLAUDE.md or AGENTS.md for agent onboarding"})
        score_b = max(score_b - 1, 0)
    sampled = all_py[:15]
    total_funcs = 0
    docstring_funcs = 0
    for f in sampled:
        try:
            txt = f.read_text(encoding="utf-8", errors="ignore")
            lines = txt.splitlines()
            for i, line in enumerate(lines):
                if line.strip().startswith("def ") and not line.strip().startswith("def _"):
                    total_funcs += 1
                    if i+1 < len(lines) and ('"""' in lines[i+1] or "'''" in lines[i+1]):
                        docstring_funcs += 1
        except:
            pass
    if total_funcs > 0 and docstring_funcs / total_funcs < 0.5:
        findings.append({"criterion":"B","severity":"P1","principle":"PP4","text":"Docstring coverage low: {}/{} public functions in sample".format(docstring_funcs, total_funcs)})
        score_b = max(score_b - 1, 0)
    adr_dir = Path(repo) / "docs" / "adr"
    if not adr_dir.exists() or not list(adr_dir.glob("*.md")):
        findings.append({"criterion":"B","severity":"P2","principle":"PP4","text":"No ADRs found in docs/adr/"})
        score_b = max(score_b - 1, 0)
    scores["B"] = score_b

    # C. Testing & Validation
    score_c = 5
    tests_dir = Path(repo) / "tests"
    if tests_dir.exists():
        test_files = list(tests_dir.rglob("test_*.py"))
        if len(test_files) == 0:
            findings.append({"criterion":"C","severity":"P1","principle":"PP7","text":"tests/ exists but no test_*.py files found"})
            score_c = max(score_c - 2, 0)
    else:
        findings.append({"criterion":"C","severity":"P0","principle":"PP7","text":"No tests/ directory"})
        score_c = 0
    if not (Path(repo) / ".coverage").exists():
        findings.append({"criterion":"C","severity":"P2","principle":"PP7","text":"No .coverage file found"})
    scores["C"] = score_c

    # D. Robustness & Error Handling
    score_d = 6
    bare_except = grep_py(r'except\s*:\s*$|except\s+Exception\s*:\s*pass', repo, "backend") + grep_py(r'except\s*:\s*$|except\s+Exception\s*:\s*pass', repo, "src")
    if bare_except:
        findings.append({"criterion":"D","severity":"P1","principle":"PP6","text":"Bare except blocks found"})
        score_d = max(score_d - 2, 0)
    for f in all_py[:50]:
        try:
            txt = f.read_text(encoding="utf-8", errors="ignore")
            if re.search(r'try:.*except.*:.*\n\s*return\s+None', txt, re.DOTALL):
                findings.append({"criterion":"D","severity":"P2","principle":"PP6","text":"try/except returning None without logging in {}".format(f.name)})
        except:
            pass
    scores["D"] = score_d

    # E. Performance & Scalability
    score_e = 5
    for f in all_py[:30]:
        try:
            txt = f.read_text(encoding="utf-8", errors="ignore")
            if re.search(r'for\s+\w+\s+in\s+.*:\s*\n.*open\(', txt):
                findings.append({"criterion":"E","severity":"P2","principle":"PP4","text":"Possible repeated file I/O inside loop in {}".format(f.name)})
        except:
            pass
    if not (Path(repo) / "benchmarks").exists():
        findings.append({"criterion":"E","severity":"P2","principle":"PP4","text":"No benchmarks/ directory"})
    scores["E"] = score_e

    # F. Code Craftsmanship
    score_f = 5
    for f in all_py:
        try:
            lines = len(f.read_text(encoding="utf-8", errors="ignore").splitlines())
            if lines > 500:
                findings.append({"criterion":"F","severity":"P0","principle":"PP1","text":"God file {} ({} lines) exceeds 500-line soft cap".format(f.name, lines)})
                score_f = max(score_f - 2, 0)
        except:
            pass
    magic = 0
    for f in all_py[:30]:
        try:
            txt = f.read_text(encoding="utf-8", errors="ignore")
            magic += len(re.findall(r'(?<![\w\d_])\d+\.\d+(?![\w\d_])', txt))
        except:
            pass
    if magic > 20:
        findings.append({"criterion":"F","severity":"P2","principle":"PP1","text":"Many magic float literals ({}) in sample".format(magic)})
    scores["F"] = score_f

    # G. Dependencies & Supply Chain
    score_g = 6
    lockfiles = ["poetry.lock", "package-lock.json", "requirements.lock", "Pipfile.lock"]
    has_lock = any((Path(repo) / lf).exists() for lf in lockfiles)
    if not has_lock:
        findings.append({"criterion":"G","severity":"P1","principle":"PP3","text":"No lockfile present (poetry.lock, package-lock.json, etc.)"})
        score_g = max(score_g - 2, 0)
    scores["G"] = score_g

    # H. Security Posture
    score_h = 7
    cred = []
    for f in all_py:
        try:
            txt = f.read_text(encoding="utf-8", errors="ignore")
            for i, line in enumerate(txt.splitlines(), 1):
                if re.search(r'(password|secret|api[_-]?key|token)\s*=\s*["\']', line, re.IGNORECASE):
                    cred.append((str(f), i))
        except:
            pass
    if cred:
        findings.append({"criterion":"H","severity":"P0","principle":"PP6","text":"Possible hard-coded credentials in source"})
        score_h = max(score_h - 3, 0)
    scores["H"] = score_h

    # I. Configuration & Environment Management
    score_i = 7
    if not (Path(repo) / ".env.example").exists():
        findings.append({"criterion":"I","severity":"P2","principle":"PP3","text":"No .env.example documenting required variables"})
        score_i = max(score_i - 1, 0)
    if not (Path(repo) / "Dockerfile").exists():
        findings.append({"criterion":"I","severity":"P2","principle":"PP3","text":"No Dockerfile for reproducible dev environment"})
    scores["I"] = score_i

    # J. Logging, Observability & Telemetry
    score_j = 5
    prints = grep_py(r'^\s*print\(', repo, "backend") + grep_py(r'^\s*print\(', repo, "src")
    if prints:
        findings.append({"criterion":"J","severity":"P2","principle":"PP6","text":"print() statements found (use logging)"})
        score_j = max(score_j - 2, 0)
    scores["J"] = score_j

    # K. Maintainability & Tech Debt
    score_k = 4
    todo_count = 0
    for d in ["backend", "tests", "frontend", "src"]:
        base = Path(repo) / d
        if base.exists():
            for f in base.rglob("*.py"):
                try:
                    txt = f.read_text(encoding="utf-8", errors="ignore")
                    for line in txt.splitlines():
                        if re.search(r'TODO|FIXME|XXX|HACK|KLUDGE', line):
                            todo_count += 1
                except:
                    pass
    if todo_count > 20:
        findings.append({"criterion":"K","severity":"P0","principle":"PP8","text":"{} TODO/FIXME/XXX comments without issue links".format(todo_count)})
        score_k = max(score_k - 3, 0)
    elif todo_count > 5:
        findings.append({"criterion":"K","severity":"P1","principle":"PP8","text":"{} TODO/FIXME/XXX comments -- link to issues".format(todo_count)})
        score_k = max(score_k - 1, 0)
    scores["K"] = score_k

    # L. CI/CD & Automation
    score_l = 4
    workflows = Path(repo) / ".github" / "workflows"
    if workflows.exists():
        wfs = list(workflows.glob("*.yml")) + list(workflows.glob("*.yaml"))
        if len(wfs) == 0:
            findings.append({"criterion":"L","severity":"P1","principle":"PP7","text":".github/workflows/ exists but no workflow files"})
            score_l = max(score_l - 3, 0)
    else:
        findings.append({"criterion":"L","severity":"P0","principle":"PP7","text":"No .github/workflows/ directory"})
        score_l = 0
    if not (Path(repo) / ".pre-commit-config.yaml").exists():
        findings.append({"criterion":"L","severity":"P2","principle":"PP7","text":"No .pre-commit-config.yaml"})
    scores["L"] = score_l

    # M. Deployment & Operability
    score_m = 6
    deploy_dir = Path(repo) / "deploy"
    if deploy_dir.exists():
        if not any((deploy_dir / f).exists() for f in ["rollback.sh", "README.md", "runbook.md"]):
            findings.append({"criterion":"M","severity":"P2","principle":"PP3","text":"deploy/ exists but no rollback.sh or runbook"})
            score_m = max(score_m - 1, 0)
    else:
        findings.append({"criterion":"M","severity":"P2","principle":"PP3","text":"No deploy/ directory"})
        score_m = max(score_m - 1, 0)
    if not (Path(repo) / "VERSION").exists():
        findings.append({"criterion":"M","severity":"P2","principle":"PP5","text":"No VERSION file"})
    scores["M"] = score_m

    # N. Compliance, Licensing & Governance
    score_n = 8
    if not (Path(repo) / "LICENSE").exists():
        findings.append({"criterion":"N","severity":"P1","principle":"PP3","text":"LICENSE file missing"})
        score_n = max(score_n - 3, 0)
    if not (Path(repo) / "SECURITY.md").exists():
        findings.append({"criterion":"N","severity":"P2","principle":"PP3","text":"SECURITY.md missing"})
        score_n = max(score_n - 1, 0)
    if not (Path(repo) / "CONTRIBUTING.md").exists():
        findings.append({"criterion":"N","severity":"P2","principle":"PP3","text":"CONTRIBUTING.md missing"})
        score_n = max(score_n - 1, 0)
    scores["N"] = score_n

    # O. Agentic Usability
    score_o = 7
    if not (Path(repo) / "CLAUDE.md").exists() and not (Path(repo) / "AGENTS.md").exists():
        findings.append({"criterion":"O","severity":"P1","principle":"PP4","text":"No agent onboarding doc (CLAUDE.md or AGENTS.md)"})
        score_o = max(score_o - 2, 0)
    if not (Path(repo) / "SPEC.md").exists():
        findings.append({"criterion":"O","severity":"P2","principle":"PP4","text":"SPEC.md missing"})
        score_o = max(score_o - 1, 0)
    scores["O"] = score_o

    overall = round(sum(scores.values()) / len(scores), 1)
    report = {
        "meta": {
            "repo": "{}/{}".format(OWNER, repo),
            "date": DATE,
            "head_short": run(["git","log","-1","--format=%h"], cwd=repo).strip(),
            "head_long": run(["git","log","-1","--format=%H"], cwd=repo).strip(),
            "branch": run(["git","rev-parse","--abbrev-ref","HEAD"], cwd=repo).strip(),
            "assessor": "pragmatic-ao-assessment-agent"
        },
        "scores": scores,
        "overall": overall,
        "findings": findings
    }

    out_dir = Path("assessments")
    out_dir.mkdir(exist_ok=True)
    (out_dir / "{}-{}-assessment.json".format(DATE, repo)).write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python assess_repo.py <repo-name>")
        sys.exit(1)
    assess(sys.argv[1])