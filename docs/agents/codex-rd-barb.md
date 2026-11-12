# Codex rd-barb wrapper

Install or sync the Codex prompt with `python3 scripts/providers/codex/install_barb.py`.
It installs `~/.codex/prompts/barb.md`, which Codex exposes as `/prompts:barb`.

Use `/barb <message>` to relay a message and `/barb chat <message>` to continue
the rd-barb thread. The wrapper calls only the shared `scripts/barb_cli.py`
entrypoint; `@barb` requests use the `rd-threads` MCP tools. Both paths return a
correlation id immediately. Replies are read from the rd-threads inbox.
