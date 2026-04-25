# Security Policy

## Supported Versions

Only the current major version receives security fixes.

| Version | Supported          |
| ------- | ------------------ |
| 1.x     | :white_check_mark: |
| < 1.0   | :x:                |

## Reporting a Vulnerability

**Please do NOT open a public GitHub issue for security vulnerabilities.**

Report security vulnerabilities via GitHub Security Advisories:

> https://github.com/D-sorganization/runner-dashboard/security/advisories/new

Provide as much detail as possible: steps to reproduce, potential impact,
and any suggested mitigations.

## Response SLA

- **Acknowledgement:** within 48 hours of receiving the report.
- **Critical vulnerabilities:** patch released within 14 days.
- **Non-critical vulnerabilities:** patch released in the next scheduled release.

We will keep you informed as the fix progresses and credit you in the release
notes unless you request otherwise.

## Out of Scope

The following are outside the scope of this security policy:

- **Denial of service on self-hosted infrastructure** — the dashboard runs on
  operator-controlled hardware; resource exhaustion attacks against that
  infrastructure are an operator responsibility.
- **Physical access attacks** — attacks that require physical access to the
  host machine running the dashboard.
- **Issues in third-party CDN dependencies** — report those to the upstream
  project.
- **Social engineering** — attacks targeting operators rather than the software.
