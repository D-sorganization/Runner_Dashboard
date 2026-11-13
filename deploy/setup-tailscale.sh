#!/usr/bin/env bash
# ==============================================================================
# setup-tailscale.sh — Secure remote access for the Runner Dashboard
# ==============================================================================
# This script:
#   1. Installs Tailscale in WSL2
#   2. Authenticates with your Tailscale account
#   3. Configures `tailscale serve` to expose the dashboard securely
#
# After running this, you can access the dashboard from any device on your
# Tailnet at https://<hostname>:8321 — no open ports, no firewall rules,
# encrypted end-to-end via WireGuard.
#
# Prerequisites:
#   - A Tailscale account (free tier at https://tailscale.com)
#   - The Runner Dashboard already running (via systemd or start-dashboard.sh)
#
# Usage:
#   cd /mnt/c/Users/<username>/Repositories/runner-dashboard
#   sed -i 's/\r$//' deploy/setup-tailscale.sh
#   chmod +x deploy/setup-tailscale.sh
#   ./deploy/setup-tailscale.sh
# ==============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC} $*"; }
ok()    { echo -e "${GREEN}[ OK ]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
fail()  { echo -e "${RED}[FAIL]${NC} $*"; exit 1; }
header(){ echo -e "\n${BOLD}═══ $* ═══${NC}"; }

PORT="${DASHBOARD_PORT:-8321}"

header "Tailscale Setup for Runner Dashboard"
echo "  Dashboard port: ${PORT}"
echo ""

# ── Step 1: Install Tailscale ────────────────────────────────────────────────
header "Step 1/3: Install Tailscale"

if command -v tailscale &>/dev/null; then
    ok "Tailscale is already installed"
    tailscale version
else
    info "Installing Tailscale via official apt repo (safer than curl|sh)..."
    curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/jammy.noarmor.gpg \
        | sudo tee /usr/share/keyrings/tailscale-archive-keyring.gpg >/dev/null
    curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/jammy.tailscale-keyring.list \
        | sudo tee /etc/apt/sources.list.d/tailscale.list
    sudo apt-get update -qq
    sudo apt-get install -y tailscale
    ok "Tailscale installed"
fi

# ── Step 2: Start Tailscale daemon and authenticate ─────────────────────────
header "Step 2/3: Authenticate"

# Start tailscaled if not running (WSL2 doesn't have systemd by default in older versions)
if ! pgrep -x tailscaled &>/dev/null; then
    info "Starting tailscaled..."
    sudo tailscaled --state=/var/lib/tailscale/tailscaled.state &>/dev/null &
    sleep 2
fi

# Check if already authenticated
TS_STATUS=$(tailscale status --json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('BackendState',''))" 2>/dev/null || echo "")

if [ "$TS_STATUS" = "Running" ]; then
    ok "Already authenticated to Tailscale"
    TS_IP=$(tailscale ip -4 2>/dev/null || echo "unknown")
    TS_HOSTNAME=$(tailscale status --self --json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('Self',{}).get('DNSName','').rstrip('.'))" 2>/dev/null || echo "unknown")
    echo "  Tailscale IP: ${TS_IP}"
    echo "  Hostname:     ${TS_HOSTNAME}"
else
    info "Please authenticate with Tailscale..."
    echo ""
    echo "  A browser window will open (or a URL will be shown)."
    echo "  Log in with your Tailscale account to connect this machine."
    echo ""
    sudo tailscale up
    ok "Authenticated!"
    TS_IP=$(tailscale ip -4 2>/dev/null || echo "unknown")
    TS_HOSTNAME=$(tailscale status --self --json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('Self',{}).get('DNSName','').rstrip('.'))" 2>/dev/null || echo "unknown")
    echo "  Tailscale IP: ${TS_IP}"
    echo "  Hostname:     ${TS_HOSTNAME}"
fi

# ── Step 3: Expose dashboard via tailscale serve ────────────────────────────
header "Step 3/3: Expose Dashboard"

info "Configuring Tailscale to serve the dashboard..."

# Reset any existing serve config
tailscale serve reset 2>/dev/null || true

# Serve the dashboard on HTTPS (Tailscale provides automatic TLS certs)
tailscale serve --bg https+insecure://localhost:${PORT} 2>/dev/null \
  || tailscale serve --bg http://localhost:${PORT} 2>/dev/null \
  || tailscale serve http://localhost:${PORT} 2>/dev/null \
  || {
    # Fallback: just use tailscale funnel for older versions
    warn "tailscale serve not available, trying direct IP access"
    echo ""
    echo "  You can access the dashboard directly at:"
    echo "  http://${TS_IP}:${PORT}"
    echo ""
  }

ok "Dashboard exposed via Tailscale"

# ── Step 4: Create systemd service for tailscaled ───────────────────────────
header "Ensuring Tailscale starts on boot"

# If systemd is available, enable the service
if command -v systemctl &>/dev/null && systemctl --version &>/dev/null 2>&1; then
    if systemctl list-unit-files tailscaled.service &>/dev/null 2>&1; then
        sudo systemctl enable tailscaled 2>/dev/null || true
        sudo systemctl start tailscaled 2>/dev/null || true
        ok "tailscaled systemd service enabled"
    else
        warn "tailscaled service not found — you may need to start it manually after reboot"
        echo "  Add to your .bashrc or .profile:"
        echo "  sudo tailscaled --state=/var/lib/tailscale/tailscaled.state &>/dev/null &"
    fi
else
    warn "systemd not fully available — adding tailscaled to startup"
    PROFILE_LINE='pgrep -x tailscaled &>/dev/null || sudo tailscaled --state=/var/lib/tailscale/tailscaled.state &>/dev/null &'
    if ! grep -qF "tailscaled" ~/.bashrc 2>/dev/null; then
        echo "" >> ~/.bashrc
        echo "# Auto-start Tailscale daemon" >> ~/.bashrc
        echo "${PROFILE_LINE}" >> ~/.bashrc
        ok "Added tailscaled auto-start to ~/.bashrc"
    else
        ok "tailscaled auto-start already in ~/.bashrc"
    fi
fi

# ── Summary ──────────────────────────────────────────────────────────────────
header "Setup Complete!"
echo ""
echo "  Your dashboard is now securely accessible from any device"
echo "  on your Tailnet (your phone, laptop, other machines)."
echo ""
echo "  ${BOLD}Access URLs:${NC}"
echo "    Local:      http://localhost:${PORT}"
echo "    Tailscale:  http://${TS_IP}:${PORT}"
if [ "$TS_HOSTNAME" != "unknown" ] && [ -n "$TS_HOSTNAME" ]; then
    echo "    DNS:        http://${TS_HOSTNAME}:${PORT}"
fi
echo ""
echo "  ${BOLD}How it works:${NC}"
echo "    - Tailscale creates a secure WireGuard mesh VPN"
echo "    - No ports are opened on your router or firewall"
echo "    - All traffic is encrypted end-to-end"
echo "    - Only devices on YOUR Tailnet can access it"
echo "    - Free for personal use (up to 100 devices)"
echo ""
echo "  ${BOLD}To access from another device:${NC}"
echo "    1. Install Tailscale on that device (tailscale.com/download)"
echo "    2. Log in with the same account"
echo "    3. Open http://${TS_IP}:${PORT} in a browser"
echo ""
echo "  ${BOLD}Useful commands:${NC}"
echo "    tailscale status          # See connected devices"
echo "    tailscale ip -4           # Show your Tailscale IP"
echo "    tailscale serve status    # Check serve config"
echo "    tailscale ping <device>   # Test connectivity"
echo ""
echo "========================================================================"
