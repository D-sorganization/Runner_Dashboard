# rd-barb Telegram setup (RD #3076)

One-time, owner-run (Dieter). Urgent questions/alerts only; `RD_BARB_TELEGRAM` stays `off` until this is
done, and the Grok relay is unaffected either way. This covers **outbound** delivery (Path B, the Bot
API); inbound reply mapping ships in a follow-up slice.

1. In Telegram, message BotFather, run `/newbot`, keep the token.
2. Put the token in the host secret store under the name `RD_BARB_TELEGRAM_BOT_TOKEN_ENV` points at
   (default `TELEGRAM_BOT_TOKEN`); it never goes in chat, an issue or a commit.
3. Record Dieter's paired chat id into `RD_BARB_TELEGRAM_CHAT_ID`.
4. Set `RD_BARB_TELEGRAM=on`.

`rd_barb_notify_telegram.select_path` is the single source of truth for which path (the Claude Channels
plugin, Path A, or this Bot API adapter, Path B) owns Telegram's one inbound `getUpdates` consumer slot,
so a provider switch hands over cleanly once both paths exist. The plugin's permission relay must stay
off either way — a Telegram reply must never approve a tool call. `python -m scripts.rd_barb_notify_telegram --json FILE --dry-run` renders a message without sending.
