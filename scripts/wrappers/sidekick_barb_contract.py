"""RD-side /barb contract for Tools Sidekick (#3071). Sidekick packaging/dispatch is
on HOLD (docs/triage only), so this module ships no Sidekick-repo command surface --
only the written contract and the shared hook entrypoint Sidekick can shell out to.
The actual Sidekick-side action/mention-handler registration is filed in Tools."""

TEMPLATE = """# {provider} <-> rd-barb contract

Two verbs, both async (return a correlation id immediately, never wait for a reply):

- `barb <message>` -- relay `<message>` to rd-barb:
  `python scripts/barb_cli.py send '{{"to": "barb", "kind": "update", "summary": "<message>"}}'`
- `barb chat` -- open or continue the conversation in rd-barb's RD staff thread
  (same command, `"summary": "chat"`); read replies there, not from this call's output.

Auth is {provider}'s own principal; the token is read from the host secret store by
the shared core and is never printed, echoed or logged.

For `@barb` mention-wake, shell out to `scripts/wrappers/sidekick_barb_hook.py`
(stdin ignored, prints unread rd-threads items addressed to `{provider}` or nothing).
It calls only `barb_cli.inbox()` -- never the hub or a message bus directly.
"""


def render(provider: str) -> str:
    return TEMPLATE.format(provider=provider)
