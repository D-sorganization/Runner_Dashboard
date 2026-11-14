---
issue: 4426
summary: "The dashboard artifact now ships clients/fleet, and the artifact smoke check fails when a client module a backend-imported script needs is missing or resolves from outside the artifact."
paths: "deploy/package-dashboard-artifact.sh,deploy/artifact_backend_import_smoke.py,tests/deploy/test_artifact_scripts_imports.py"
---
