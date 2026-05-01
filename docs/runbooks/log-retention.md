# Log Retention Runbook

**Issue:** #418 — Log shipping: Vector sidecar config + retention policy

## Overview

Runner-dashboard logs are shipped from each fleet node via a Vector sidecar
to a central Loki endpoint. This runbook covers:

1. How retention is enforced
2. How to configure and verify the Vector sidecar
3. How to adjust retention policies

---

## Retention Policy

| Log tier | Storage | Retention |
|----------|---------|-----------|
| Application logs (info/warn) | Loki | **7 days** |
| Error / critical logs | Loki | 30 days |
| Journald on-host | systemd-journald | Max 1 GB / 30 days |
| Docker json-file driver | local | 7 × 100 MB rotated files |

The `retention` Loki label is set by Vector's transform and used by the
Loki compactor's retention rules. Configure compactor rules in your Loki
config:

```yaml
# loki/config.yaml (excerpt)
compactor:
  retention_enabled: true
  retention_delete_delay: 2h
  retention_delete_worker_count: 150

chunk_store_config:
  max_look_back_period: 30d

limits_config:
  retention_period: 7d
  # Per-stream override for error logs
  per_stream_rate_limit: 10MB
```

---

## Systemd-Journald Retention (Host)

Apply the retention drop-in to all fleet nodes:

```bash
sudo mkdir -p /etc/systemd/journald.conf.d
sudo cp deploy/observability/journald-retention.conf \
       /etc/systemd/journald.conf.d/runner-dashboard.conf
sudo systemctl restart systemd-journald

# Verify
journalctl --disk-usage
# Expected: SystemMax is 1G, retention 30d
```

---

## Vector Sidecar

### Standalone (non-Docker)

```bash
# Install Vector
curl -1sLf 'https://repositories.timber.io/public/vector/cfg/setup/bash.deb.sh' | bash
apt-get install vector

# Deploy config
sudo cp deploy/observability/vector.toml /etc/vector/vector.toml

# Set environment
echo "LOKI_URL=http://your-loki:3100" | sudo tee -a /etc/vector/env
echo "FLEET_NODE_NAME=$(hostname -s)" | sudo tee -a /etc/vector/env

# Enable and start
sudo systemctl enable vector
sudo systemctl start vector
sudo systemctl status vector
```

### Docker Compose (recommended for new deployments)

```bash
export GH_TOKEN=ghp_your_token
export LOKI_URL=http://your-loki:3100
export FLEET_NODE_NAME=$(hostname -s)

cd docker
docker compose up -d

# Verify logs are shipping
docker compose logs vector --tail=20
```

---

## Verification

### Check Vector is receiving and forwarding logs

```bash
# Tail Vector's own output
journalctl -u vector -f --since "5 minutes ago"

# Or in Docker Compose mode:
docker compose logs -f vector

# Verify Loki is receiving streams (requires curl + jq):
curl -s "${LOKI_URL}/loki/api/v1/labels" | jq '.data[]'
# Expected: ["job", "level", "node", "retention"]

# Query last 10 error logs:
curl -sG "${LOKI_URL}/loki/api/v1/query_range" \
  --data-urlencode 'query={job="runner-dashboard", level="error"}' \
  --data-urlencode "limit=10" | jq '.data.result[].values[:3]'
```

### Check journald disk usage

```bash
journalctl --disk-usage
# Verify SystemMax respected (should not exceed 1G)
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| Vector exits immediately | Bad TOML syntax | `vector validate /etc/vector/vector.toml` |
| No logs in Loki | Wrong `LOKI_URL` | `curl $LOKI_URL/ready` must return 200 |
| Journald filling disk | Drop-in not applied | Re-run the copy + restart steps above |
| Docker socket permission denied | User not in `docker` group | `usermod -aG docker $(whoami)` |

---

## Alerts

Set up the following Grafana / Alertmanager alerts on the Loki data:

```yaml
# grafana-alerts.yaml (pseudo-config)
- name: DashboardErrorSpike
  expr: 'sum(count_over_time({job="runner-dashboard", level="error"}[5m])) > 20'
  for: 2m
  annotations:
    summary: "runner-dashboard error rate spike on {{ $labels.node }}"

- name: DashboardLogsAbsent
  expr: 'absent(rate({job="runner-dashboard"}[10m]))'
  for: 5m
  annotations:
    summary: "runner-dashboard logs missing — Vector sidecar may be down"
```
