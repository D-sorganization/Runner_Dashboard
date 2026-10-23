#!/usr/bin/env bash
# Provision the filtered phase-A build network on a security-scan VM (#2572).
#
# Panel verdict q4 item 5: image builds for security scans need network access to
# fetch dependencies, but must not reach the fleet. Run this ON THE SCAN VM, never
# on ControlTower or a runner host. It creates:
#
#   * docker network NETWORK (bridge BRIDGE, IPv6 off)
#   * iptables chain VS_BUILD, hooked from DOCKER-USER and INPUT for that bridge.
#     DNS is allowed only to fixed public resolvers. RFC1918, CGNAT/tailnet
#     (100.64/10), link-local/metadata (169.254/16), loopback and multicast are
#     dropped. Nothing on the bridge may reach the VM itself.
#   * buildx builder NETWORK-builder (docker-container driver) on NETWORK, which is
#     the builder vuln_scan.executor.builder_name() targets. Its buildkitd is
#     configured with the same fixed resolvers, so builds never use the VM's own
#     resolv.conf (a LAN router or MagicDNS, which VS_BUILD drops).
#   * the marker MARKER_DIR/NETWORK.ok holding the network id, written last. It
#     lives on tmpfs (/run), so a reboot clears it, and the dashboard refuses to
#     scan until this script has re-applied the filter (panel q5).
#
# Default is a dry run that prints the commands. Pass --apply to execute. The
# script is idempotent; vs-build-network.service runs it at every boot, because
# iptables rules are not persistent.
#
# Usage: setup-build-network.sh [--apply] [--network vs-build] [--dns "1.1.1.1 9.9.9.9"]
set -euo pipefail

NETWORK="vs-build"
BRIDGE="br-vsbuild"
DNS_SERVERS="1.1.1.1 9.9.9.9"
MARKER_DIR="/run/vs-build"
APPLY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply) APPLY=1 ;;
    --network) NETWORK="$2"; shift ;;
    --dns) DNS_SERVERS="$2"; shift ;;
    -h|--help) sed -n '2,27p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

if [[ ! "$NETWORK" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$ ]]; then
  echo "refusing: bad network name '$NETWORK'" >&2
  exit 2
fi
for dns in $DNS_SERVERS; do
  if [[ ! "$dns" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]]; then
    echo "refusing: DNS server '$dns' is not an IPv4 address" >&2
    exit 2
  fi
done
case "$(hostname | tr '[:upper:]' '[:lower:]')" in
  *controltower*) echo "refusing: ControlTower never hosts security scans" >&2; exit 3 ;;
esac

run() {
  if [[ "$APPLY" == 1 ]]; then
    "$@"
  else
    printf 'DRY RUN:'; printf ' %q' "$@"; printf '\n'
  fi
}

# 1. Network (no IPv6) with a fixed bridge name the firewall rules can match.
if ! docker network inspect "$NETWORK" >/dev/null 2>&1; then
  run docker network create --driver bridge --ipv6=false \
    --opt "com.docker.network.bridge.name=$BRIDGE" \
    --label fleet.security.scan=1 "$NETWORK"
fi
run sysctl -w "net.ipv6.conf.$BRIDGE.disable_ipv6=1"

# 2. Firewall: rebuild the VS_BUILD chain from scratch each run.
run iptables -N VS_BUILD 2>/dev/null || true
run iptables -F VS_BUILD
for dns in $DNS_SERVERS; do
  run iptables -A VS_BUILD -d "$dns" -p udp --dport 53 -j RETURN
  run iptables -A VS_BUILD -d "$dns" -p tcp --dport 53 -j RETURN
done
run iptables -A VS_BUILD -p udp --dport 53 -j DROP
run iptables -A VS_BUILD -p tcp --dport 53 -j DROP
for cidr in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10 \
            169.254.0.0/16 127.0.0.0/8 0.0.0.0/8 224.0.0.0/4 240.0.0.0/4; do
  run iptables -A VS_BUILD -d "$cidr" -j DROP
done
run iptables -A VS_BUILD -j RETURN
if ! iptables -C DOCKER-USER -i "$BRIDGE" -j VS_BUILD 2>/dev/null; then
  run iptables -I DOCKER-USER 1 -i "$BRIDGE" -j VS_BUILD
fi
# Containers on the bridge may not talk to the VM itself (dockerd, sshd, ...).
if ! iptables -C INPUT -i "$BRIDGE" -j DROP 2>/dev/null; then
  run iptables -I INPUT 1 -i "$BRIDGE" -j DROP
fi
run ip6tables -I FORWARD 1 -i "$BRIDGE" -j DROP

# 3. The buildx builder whose buildkitd container lives on the filtered network.
#    It is recreated each run so its buildkitd.toml always pins DNS_SERVERS.
BUILDER="$NETWORK-builder"
BUILDKITD_TOML="$MARKER_DIR/$NETWORK.buildkitd.toml"
nameservers=""
for dns in $DNS_SERVERS; do nameservers="$nameservers\"$dns\","; done
run mkdir -p "$MARKER_DIR"
run rm -f "$MARKER_DIR/$NETWORK.ok"
if [[ "$APPLY" == 1 ]]; then
  printf '[dns]\n  nameservers = [%s]\n' "${nameservers%,}" > "$BUILDKITD_TOML"
else
  printf 'DRY RUN: write %s: [dns] nameservers = [%s]\n' "$BUILDKITD_TOML" "${nameservers%,}"
fi
if docker buildx inspect "$BUILDER" >/dev/null 2>&1; then
  run docker buildx rm --force "$BUILDER"
fi
run docker buildx create --name "$BUILDER" --driver docker-container \
  --driver-opt "network=$NETWORK" --config "$BUILDKITD_TOML"
run docker buildx inspect --bootstrap "$BUILDER"

# 4. The marker the dashboard checks before every real scan. Written last, so a
#    failure above (set -e) leaves no marker and scans stay refused.
if [[ "$APPLY" == 1 ]]; then
  docker network inspect --format '{{.Id}}' "$NETWORK" > "$MARKER_DIR/$NETWORK.ok"
  chmod 0644 "$MARKER_DIR/$NETWORK.ok"
else
  echo "DRY RUN: write $MARKER_DIR/$NETWORK.ok (network id)"
fi

echo "done (apply=$APPLY): network=$NETWORK bridge=$BRIDGE builder=$BUILDER dns='$DNS_SERVERS'"
