### Fixed
- Recognize bare `d-sorg-fleet*` labels in `shared_scripts/queued_job_diagnostic.py` as self-hosted fleet jobs so unroutable labels like `d-sorg-fleet-public-heavy` classify as `label_mismatch` instead of `github_hosted` (#2897).
