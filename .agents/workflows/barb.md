---
description: Relay a message to rd-barb, or open/continue her RD staff thread
---

`/barb <message>` relays `<message>` to rd-barb and returns a correlation id immediately
(async -- it never waits for a model reply). `/barb chat` opens or continues the
conversation in rd-barb's RD staff thread; read replies there, not from this workflow's
output.

Auth is agy's own principal; the token is read from the host secret store by the
shared core and is never printed, echoed or logged.

Run exactly one of these, then print the JSON result (it includes `correlation_id`) and
stop -- do not poll for a reply:

- `python scripts/barb_cli.py send '{"to": "barb", "kind": "update", "summary": "<message>"}'`
- `python scripts/barb_cli.py send '{"to": "barb", "kind": "update", "summary": "chat"}'`
  for `/barb chat`
