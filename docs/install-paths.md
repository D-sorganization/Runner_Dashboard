# Supported Install Paths & Configuration/Secret Separation

Part of the deployment and release hardening epic (#3727). This page is the
single answer to two questions an operator asks before installing the
Runner Dashboard: **which install method is supported**, and **what is
config versus what is a secret**.

## Supported install paths

Two install paths are supported. Both run the same backend/frontend code;
they differ only in how the process is supervised and how configuration
reaches it.

### 1. systemd bare-metal install (primary; fleet-internal)

The dashboard runs as a long-lived `systemd` service on the host, updated
in place. This is the path the fleet's own machines use.

- Bootstrap: `bash deploy/setup.sh --runners N --machine-name <name> [--role hub|node]`
- Routine update: `bash deploy/update-deployed.sh`
- Rollback: `bash deploy/rollback.sh`

Full detail, including the file layout, systemd sandboxing
(`ReadWritePaths=`), and token refresh: [`docs/deployment-model.md`](deployment-model.md).

### 2. Docker Compose install (containerized)

The dashboard and its log-shipping sidecar run as containers, built from
the repo's own [`Dockerfile`](../Dockerfile) and brought up with
[`docker/docker-compose.yml`](../docker/docker-compose.yml):

```bash
cp .env.example .env   # fill in GH_TOKEN, SESSION_SECRET, etc.
cd docker
docker compose up -d
```

This path has no systemd unit, no `deploy/setup.sh` bootstrap step, and no
host sudoers configuration — the container boundary replaces that
isolation. It is the path to use for a standalone install outside the
fleet's own hosts (for example, evaluating the dashboard on a laptop or a
single server).

### Not currently supported

There is no Windows-native installer and no Helm chart/Kubernetes
manifest. A published, versioned, multi-arch container image with the same
build attestation and SBOM guarantees as the existing tarball release
([#3724](https://github.com/D-sorganization/Runner_Dashboard/issues/3724),
see [`docs/releases.md`](releases.md)), explicit upgrade/rollback guarantees
(#3725), and a clean-host install/upgrade/rollback smoke test (#3726) are
tracked separately under the same epic (#3727) and are not yet in place for
either path above.

## Configuration vs. secret separation

The two install paths use different mechanics (a mode-0600 env file for
systemd, a `.env`/host-env for Docker Compose), but the same separation
rule applies to both: **non-secret configuration may default to a
placeholder value and may be documented in the repo; a secret must never
be committed, and is supplied only at the deploy target.**

| Category            | Examples                                                                                                 | Where it lives                                                                                                                                                                                   | Committed to git?                                                     |
| ------------------- | -------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------- |
| Config (non-secret) | `GITHUB_ORG`, `DASHBOARD_PORT`, `NUM_RUNNERS`, `DISPLAY_NAME`, `DASHBOARD_LOG_LEVEL`                     | `.env.example` (documented defaults); `config/*.json` on a deployed host                                                                                                                         | Yes, as placeholders/examples only — never with a real per-host value |
| Secret              | `GH_TOKEN`, `SESSION_SECRET`, `GITHUB_CLIENT_SECRET`, `ANTHROPIC_API_KEY`, `GITHUB_APP_PRIVATE_KEY_FILE` | systemd: `~/.config/runner-dashboard/env` (mode `0600`, loaded by the unit, outside the deploy tree). Docker: a local `.env` file or the host's own environment, passed into `docker compose up` | Never                                                                 |

Both `.env` and `.env.*` are excluded repo-wide (see `.gitignore`), so a
`docker compose` operator who copies `.env.example` to `.env` cannot
accidentally commit the filled-in secrets. The systemd path keeps secrets
out of the deploy tree entirely, in a file the deploy/update scripts never
touch.

Required secrets for the Docker Compose path, as declared in
`docker/docker-compose.yml`:

| Variable              | Required                           | Notes                                                                                                                           |
| --------------------- | ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| `GH_TOKEN`            | Yes                                | GitHub PAT with `admin:org` and `repo` scopes, or App credentials per [`docs/deployment-model.md`](deployment-model.md#secrets) |
| `SESSION_SECRET`      | Yes for networked/production use   | At least 32 whitespace-free characters; see `.env.example`                                                                      |
| `ORG`                 | No (defaults to `D-sorganization`) | Non-secret                                                                                                                      |
| `DASHBOARD_LOG_LEVEL` | No (defaults to `INFO`)            | Non-secret                                                                                                                      |

Rotating a secret never requires a code change or a commit on either
path: refresh the host env file (systemd) or `.env` (Docker) and restart
the service/container.
