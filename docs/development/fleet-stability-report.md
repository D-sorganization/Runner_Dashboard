# Fleet Stability Report – Windows vs WSL Runners

**Purpose:** Evaluate the stability implications of transitioning the self-hosted GitHub Actions runner fleet from WSL-based environments to native Windows environments, specifically considering our current project requirements.

## Current State (WSL)

Currently, our runners are operating within WSL on Windows host machines.

**Pros:**

- Provides a native Linux environment, which is required by many of our project tests (especially Python, C++, and Docker workflows).
- Seamless bash scripting and systemd integrations (where supported).

**Cons:**

- High unreliability: WSL frequently goes into aggressive resource saving modes (e.g. idle-timeout suspending), causing runners to go offline and jobs to blackhole.
- Keepalive systems (wsl_watchdog.py, .wslconfig, scheduled tasks) are brittle and often fail to prevent suspension.
- Complex networking bridges and disk I/O translation across the Windows/WSL boundary.

## Alternative State (Native Windows Runners)

Running the GitHub Actions runner directly as a Windows service on the host machine.

**Pros:**

- Extreme stability: The runner operates as a native Windows service, completely immune to WSL lifecycle suspensions.
- Better hardware access: Direct access to GPUs, memory, and full CPU cores without virtualization overhead.
- Simple, reliable lifecycle management via standard Windows Services.

**Cons:**

- **Incompatible Environments:** Tests that require Linux environments will break unless they are updated to run in Windows natively or executed inside Docker containers.
- Shell scripts (like \*.sh hooks or CI steps) must be translated to PowerShell, or we must rely on Git Bash (which introduces its own quirks).
- Significant refactoring required across multiple repositories that currently assume a POSIX-compliant execution environment.

## Update (May 2026)

Local Windows runners are currently offline on this machine. This demonstrates that even native Windows environments require robust monitoring and lifecycle management. While they avoid WSL-specific suspension issues, they still require a host-level self-healing strategy to guarantee uptime when the physical or virtual host experiences disruptions or service failures.

## Recommendation for the Fleet

Given that a large percentage of our repositories rely on WSL environments for testing, **a wholesale migration to Native Windows Runners is not recommended at this time, as the refactoring cost across the fleet would be massive.**

Instead, we should maintain the WSL runners but implement a **Self-Healing WSL Strategy** on the Runner Dashboard:

1. **Active Health Probes:** Configure the dashboard to actively ping a lightweight service running inside WSL.
2. **Automated Recovery:** If the ping fails (indicating WSL has suspended or crashed), the dashboard's Windows-side watchdog triggers wsl.exe --shutdown and restarts the service, transparently recovering the node before jobs are queued.
3. **Optimized Cache Usage:** Ensure ctions/cache is heavily utilized to offset any performance penalties incurred by WSL filesystem bridging.
