# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `backend/pyproject.toml` and `backend/uv.lock` for reproducible backend dependency resolution.
- Root-level `uv.lock` for reproducible project dependency resolution.

### Changed

- Extracted security utilities from `backend/server.py` into `backend/security.py` to begin god-module refactoring.

### Fixed

- Corrected broken issue reference `#944` to `#161` in `pyproject.toml` and CI workflow.
- Restored missing `PROVIDERS_WITH_MODEL` definition in frontend bundle.
- Removed stale `agent_remediation_140.py` from repo root and added `/*_[0-9]*.py` to `.gitignore`.

## [4.0.1] - 2026-04-26

### Fixed

- CSP: kept `strict-dynamic` in the `script-src` directive (it remains
  required for compatibility with the CDN-loaded React bootstrap and is
  still present in `backend/middleware.py`); restored `'unsafe-inline'`
  on `style-src` to fix the blank-dashboard regression (#172).

  Note: an earlier draft of this changelog entry stated that
  `strict-dynamic` had been removed. That was inaccurate — the directive
  was retained. This entry has been corrected (issue #394).

## [4.0.0] - 2026-04-25

### Added

- Queue stale-queue detection, bulk purge, and scheduled auto-cleanup.
- Principal Management UI scaffolding.
- Identity impersonation flow and `SPEC.md` updates (Epic #63 Wave 5).
- Fair Sharing UI (Wave 3).

### Fixed

- Prevented pytest failures from being silently masked in CI (#148).

## [3.0.0] - earlier

### Added

- Initial Runner Dashboard with FastAPI backend and React SPA frontend.
- GitHub OAuth login flow and service-token support.
- Fleet-wide runner coordination and job queue management.
- CSP and security headers.