"""Shared /barb slash-command template (#3067): one source, rendered per provider so each
fleet host's ``/barb`` / ``@barb`` wrapper stays identical (DRY). This module only renders
text; it never touches the shared rd-threads entrypoint itself."""

TEMPLATE = """---
description: Relay a message to rd-barb, or open/continue her RD staff thread
---

`/barb <message>` relays `<message>` to rd-barb and returns a correlation id immediately
(async -- it never waits for a model reply). `/barb chat` opens or continues the
conversation in rd-barb's RD staff thread; read replies there, not from this command's
output.

Auth is {provider}'s own principal; the token is read from the host secret store by the
shared core and is never printed, echoed or logged.

Run exactly one of these, then print the JSON result (it includes `correlation_id`) and
stop -- do not poll for a reply:

- `python scripts/barb_cli.py send '{{"to": "barb", "kind": "update", "summary": "<message>"}}'`
- `python scripts/barb_cli.py send '{{"to": "barb", "kind": "update", "summary": "chat"}}'`
  for `/barb chat`
"""


def render(provider: str) -> str:
    return TEMPLATE.format(provider=provider)
