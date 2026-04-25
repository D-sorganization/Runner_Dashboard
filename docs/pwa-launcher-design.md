# PWA Native Launcher Design — Issue #61

**Status:** Design Phase  
**Version:** 1.0  
**Date:** 2026-04-25

---

## Problem Statement

The dashboard can be installed as a Progressive Web App (PWA) or Chrome app, but browser sandboxing prevents the PWA from directly starting native processes (wsl.exe, systemctl, PowerShell). Operators want launching the dashboard to also ensure the backend and runner services are running.

**Current Limitation:**
- PWA can open `http://localhost:8321` but cannot verify/start the backend
- If the backend crashes or stops, the PWA shows a connection error with no recovery path
- Operators must manually restart services or use a terminal

**Desired UX:**
- Single click (PWA icon) starts backend + opens dashboard
- If backend is already running, just opens the dashboard
- If backend crashes, UI offers one-click recovery
- All recovery actions require explicit operator intent (no silent auto-restart)

---

## Architecture Options Evaluation

### Option 1: Windows Scheduled Task + Recovery Status UI

**Approach:**
- Dashboard backend runs as a Windows Scheduled Task (keepalive trigger every 5 min)
- PWA detects backend down via failed API calls
- Shows recovery status panel with instruction or one-click "Start Now" button
- No custom protocol or native bridge needed

**Pros:**
- ✅ Zero new dependencies or binaries
- ✅ Uses native Windows scheduling (systemd equivalent)
- ✅ UI can show clear status: running / starting / error
- ✅ Completely local, no security bridge required

**Cons:**
- ❌ Cannot launch backend from PWA directly (requires manual click or terminal)
- ❌ Still a two-step UX: launch PWA, then click "Start" if backend is down
- ❌ Task scheduler visibility is poor (no easy way to check from app)
- ❌ Cross-platform support is Windows-only

**Best for:** Development, testing, environments where manual startup is acceptable

**Risk:** Low — uses existing Windows infrastructure, no new attack surface

---

### Option 2: Custom URL Protocol Handler (`runner-dashboard://start`)

**Approach:**
- Install a signed local launcher script (PowerShell/batch) that handles `runner-dashboard://start` URLs
- Script starts backend services, waits for health check, opens browser to dashboard
- PWA detects backend down and offers a button that triggers the protocol URL
- Launcher script must be signed to prevent spoofing

**Pros:**
- ✅ One-click recovery from PWA: `<a href="runner-dashboard://start">Start Dashboard</a>`
- ✅ Browser security model prevents non-HTTPS sites from exploiting it
- ✅ Script is local, signed, and operator controls installation
- ✅ Works on Windows and macOS (via custom URL schemes)

**Cons:**
- ⚠️ Requires operator to install/register protocol handler once
- ⚠️ Script signing adds build complexity (self-signed is okay for local)
- ❌ Not supported on Linux desktops (custom protocols less standardized)
- ⚠️ Requires operator trust: "Do you want to allow this site to launch an app?"

**Best for:** Windows/macOS desktop installations with one-time setup

**Risk:** Medium — requires operator approval each time, but protocol is signed and local-only

---

### Option 3: Native Helper Service (HTTP bridge)

**Approach:**
- Small local service listens on localhost:9000
- Exposes `/start`, `/stop`, `/status` endpoints for backend control
- Requires elevated permissions (runs as admin/root)
- PWA calls the helper API to start services, no native bridge visible to user

**Pros:**
- ✅ True one-click recovery from PWA (no extra dialogs)
- ✅ Can manage multiple services (backend, runners, autoscaler)
- ✅ Centralized control point for recovery actions
- ✅ Operator doesn't see launcher details (abstracted)

**Cons:**
- ❌ Requires separate binary/installer for each platform (Windows/Linux/macOS)
- ❌ Requires elevated permissions (admin/root) — security review needed
- ❌ More code to maintain and secure
- ❌ Adds a long-lived process that can crash
- ❌ Cross-platform support requires multiple implementations

**Best for:** Enterprise deployments wanting abstraction and unified control

**Risk:** High — introduces a new privileged service, requires security hardening

---

### Option 4: Desktop Shortcuts (OS-native)

**Approach:**
- During setup, create `.lnk` (Windows) or `.desktop` (Linux) shortcut file
- Shortcut runs a startup script that:
  1. Starts services (wsl.exe, systemctl)
  2. Waits for health check (curl http://localhost:8321)
  3. Opens browser to `http://localhost:8321`
- No PWA involvement in startup, shortcuts are the primary launch mechanism
- PWA can be opened directly once backend is running

**Pros:**
- ✅ Standard OS mechanism, no custom protocols or helpers
- ✅ Shortcuts visible and accessible on desktop/start menu
- ✅ Simple shell script, easy to understand and audit
- ✅ Works on all platforms (Windows/Linux/macOS)
- ✅ No permission dialogs or operator approval needed

**Cons:**
- ❌ PWA cannot trigger startup directly (two-step: click shortcut, then click PWA)
- ❌ Not ergonomic for "installed app" experience
- ❌ Requires operator to use shortcut instead of PWA icon
- ❌ PWA loses its primary value proposition

**Best for:** Environments where PWA is secondary to desktop shortcuts

**Risk:** Low — standard approach, no new security model

---

## Recommendation: **Option 2 (Custom URL Protocol) with Option 1 (Status UI) Fallback**

### Rationale

- **Operator familiarity:** Operators expect "click icon → app launches"; Option 2 closest matches this
- **Security alignment:** Protocol handler runs in operator context, signed script prevents spoofing
- **Simplicity:** No new service, no elevated permissions beyond script execution
- **Multi-platform:** Works on Windows and macOS; Linux gets Option 1 fallback
- **Graceful degradation:** If protocol not registered, UI shows status + manual instructions

### Implementation Design

#### Phase 1: Windows/macOS Support (Custom Protocol)

**Components:**

1. **Launcher Script** (`deploy/launcher.ps1` on Windows, `deploy/launcher.sh` on macOS)
   - Takes action: `start` (start services, open dashboard) or `status` (check health)
   - Starts backend via systemd/WSL/native service
   - Performs HTTP health check (retry up to 10 times, 1s interval)
   - Opens browser to `http://localhost:8321` on success
   - Returns exit code 0 (success) or non-zero (failure)
   - Logs to `~/.config/runner-dashboard/launcher.log`

2. **Registration Script** (`deploy/register-protocol.ps1` on Windows)
   - Registers `runner-dashboard://` protocol handler pointing to launcher
   - Requires operator approval once (native Windows "Allow app?" dialog)
   - Installer calls this during setup
   - Can be re-run if handler is lost

3. **Backend Health Check Endpoint** (new in `backend/server.py`)
   ```python
   @router.get("/health", tags=["diagnostics"])
   async def health_check() -> dict:
       """Launcher health check. Returns 200 if backend is ready."""
       return {"status": "ready", "timestamp": datetime.now(UTC).isoformat()}
   ```

4. **Frontend Recovery UI** (new in `frontend/index.html`)
   - Periodically pings `/health` endpoint
   - If backend unreachable for >5 seconds, shows modal:
     - "Dashboard backend is not responding"
     - Buttons:
       - "Start Now" → triggers `runner-dashboard://start` (Windows/macOS)
       - "Manual Instructions" → expands textual steps (Linux fallback)
       - "Refresh" → re-check and close modal if successful
   - On success, auto-closes modal

#### Phase 2: Linux Support (Status UI Fallback)

For Linux (where custom protocols are non-standard):

1. **No custom protocol; rely on systemd service auto-start**
   - Systemd service has `Restart=on-failure` and keepalive mechanism
   - If down, operator uses terminal or system UI to restart

2. **Dashboard detects down backend and shows:**
   - "Backend is not responding. Systemd service may have crashed."
   - Instructions: `systemctl restart runner-dashboard`
   - "Refresh" button to re-check

3. **Optional:** System tray icon (via native Nautilus/Dolphin integration) on Linux if PWA is pinned

#### Phase 3: Operator Documentation

**In `docs/operator-guide.md` and `SPEC.md`:**

1. Installation steps
   - "Setup will register the `runner-dashboard://start` protocol handler"
   - "Click 'Allow' when Windows/macOS asks for permission"

2. Usage
   - "Click the dashboard PWA icon to launch"
   - "If backend is not responding, click 'Start Now' in the error modal"
   - Platform-specific sections for Windows/macOS/Linux

3. Troubleshooting
   - Protocol handler missing? Run `deploy/register-protocol.ps1` again
   - Services won't start? Check `launcher.log` for errors
   - Health check timeout? Increase `LAUNCHER_HEALTH_TIMEOUT_SECS` env var

---

## Implementation Checklist

- [ ] Add `/health` endpoint to `backend/server.py`
- [ ] Create `deploy/launcher.ps1` (Windows PowerShell script)
- [ ] Create `deploy/launcher.sh` (macOS/Linux script)
- [ ] Create `deploy/register-protocol.ps1` (Windows registry handler)
- [ ] Update `deploy/setup.sh` to call `register-protocol.ps1` (Windows only)
- [ ] Add recovery modal component to `frontend/index.html`
- [ ] Add health check polling logic (useEffect with retry backoff)
- [ ] Update `SPEC.md` Section 6 with launcher architecture
- [ ] Update operator guide in `docs/deployment-model.md`
- [ ] Test on Windows (systemd + WSL)
- [ ] Test on macOS (native services)
- [ ] Test on Linux (systemd fallback)
- [ ] Add logging to launcher scripts for debugging

---

## Security Considerations

**Custom Protocol Handler:**
- ✅ Only registered for `runner-dashboard://` scheme (no collision with other apps)
- ✅ Script is local, operator-controlled, with no network access
- ✅ Operator explicitly approves protocol handler installation
- ✅ Browser prevents non-local sites from triggering the protocol
- ✅ Launcher script has no shell expansion (hardcoded paths)

**Health Check Endpoint:**
- ✅ No authentication required (endpoint is internal localhost:8321)
- ✅ Returns minimal data (status + timestamp only)
- ✅ No secrets or operational state exposed

**Recovery UI Modal:**
- ✅ "Manual Instructions" path requires operator to use terminal
- ✅ Protocol handler requires operator to click "Allow" in browser
- ✅ No automatic remediation; all actions explicit

---

## Success Criteria

1. ✅ PWA icon click launches dashboard (Windows/macOS)
2. ✅ If backend is down, recovery modal appears automatically
3. ✅ "Start Now" button successfully starts backend and opens dashboard
4. ✅ No manual terminal commands needed for happy path
5. ✅ All recovery actions logged for audit purposes
6. ✅ Cross-platform (Windows/macOS/Linux with fallbacks)
7. ✅ Zero new secrets or credential exposure

---

## Next Steps

1. **Design approval:** Validate architecture with team
2. **Phase 1 implementation:** Windows + macOS custom protocol handler
3. **Testing:** Manual testing on Windows 11, macOS, Ubuntu 22.04
4. **Phase 2 rollout:** Update deployment scripts and documentation
5. **Phase 3 (optional):** Enhanced Linux support if demand exists

