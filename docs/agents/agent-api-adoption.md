# Fleet Agent API Standard v1 — Runner Dashboard adoption

Repository_Management [#2885](https://github.com/D-sorganization/Repository_Management/issues/2885) defines the fleet-wide standard; this page records how **Runner_Dashboard** adopts the **ops MCP** subset (issue [#4305](https://github.com/D-sorganization/Runner_Dashboard/issues/4305)).

Runner Dashboard coordinates the fleet (briefings, claims, staff, Barb threads). It does **not** run model execution; model MCPs stay in sibling code repositories.

## Ops MCP servers

| Server id      | Entry point                         | `.mcp.json` key |
| -------------- | ----------------------------------- | --------------- |
| `fleet`        | `clients/fleet/fleet_mcp.py`        | `fleet`         |
| `rd-threads`   | `scripts/rd_threads_mcp.py`         | `rd-threads`    |
| `scan_sandbox` | `backend/vuln_scan/scan_mcp.py`     | _(executor only — not in `.mcp.json`)_ |

## Conformance subset (CI)

`tests/test_ops_mcp_conformance.py` enforces:

| Check                    | Status   | Notes |
| ------------------------ | -------- | ----- |
| Bounded `tools/list`     | Enforced | `MAX_TOOLS_LIST_ITEMS` in `clients/fleet/ops_mcp_contract.py` |
| Structured tool errors   | Enforced | `error`, `code`, `message`, `retryable` on tool error payloads |
| `contract_version` field | Enforced | `runner-dashboard-ops-mcp/1.0.0` on tool JSON payloads |
| Stdio JSON-RPC handshake | Enforced | subprocess JSON-RPC + real `mcp` SDK session smoke |
| **run-contract**         | **N/A**  | RD does not execute model runs or emit v1 run records |

Copy the **run-contract** N/A row into the RM spec adoption table when that document is updated upstream.

## Local check

```bash
python -m scripts.check_ops_mcp_conformance
```
