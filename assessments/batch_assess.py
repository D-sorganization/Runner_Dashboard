#!/usr/bin/env python3
"""Batch A-O assessment for all D-Sorganization repos."""

import json
import subprocess
from pathlib import Path

REPOS = [
    "Bitnet_Launcher",
    "Games",
    "Maxwell-Daemon",
    "MLProjects",
    "Playground",
    "QuatEngine",
    "Tools_Private",
    "UpstreamDrift",
    "Worksheet-Workshop",
]

results = []
for repo in REPOS:
    if not Path(repo).exists():
        continue
    try:
        out = subprocess.check_output(
            ["python", "assessments/assess_repo.py", repo], stderr=subprocess.STDOUT, text=True, timeout=120
        )
        data = json.loads(out)
        results.append(
            {
                "repo": repo,
                "status": "ok",
                "overall": data.get("overall"),
                "findings_count": len(data.get("findings", [])),
                "scores": data.get("scores"),
            }
        )
    except Exception as e:
        results.append({"repo": repo, "status": "error", "error": str(e)})

print(json.dumps(results, indent=2))
