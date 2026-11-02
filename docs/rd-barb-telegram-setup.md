# rd-barb Telegram setup (RD #3076, #3805)

One-time, owner-run (Dieter). Urgent questions/alerts only; `RD_BARB_TELEGRAM` stays `off` until this is
done, and the Grok relay is unaffected either way. Covers both outbound delivery and inbound reply
mapping on Path B (the Bot API).

1. In Telegram, message BotFather, run `/newbot`, keep the token.
2. Put the token in the host secret store under the name `RD_BARB_TELEGRAM_BOT_TOKEN_ENV` points at
   (default `TELEGRAM_BOT_TOKEN`); it never goes in chat, an issue or a commit.
3. Record Dieter's paired chat id into `RD_BARB_TELEGRAM_CHAT_ID`.
4. Set `RD_BARB_TELEGRAM=on`.

`rd_barb_notify_telegram.select_path` is the single source of truth for which path (the Claude Channels
plugin, Path A, or this Bot API adapter, Path B) owns Telegram's one inbound `getUpdates` consumer slot,
so a provider switch hands over cleanly once both paths exist. The plugin's permission relay must stay
off either way — a Telegram reply must never approve a tool call. `python -m scripts.rd_barb_notify_telegram --json FILE --dry-run` renders a message without sending.

On Path B, replying "approve" or "deny" (quoting the message's `Ref:` correlation id, or via an
inline-keyboard button whose `callback_data` is `"<decision>:<correlation_id>"`) routes the answer
through `scripts.rd_barb_telegram_inbound.TelegramInboundPoller`. A reply with an unknown or expired
correlation id becomes a plain dashboard-inbox note rather than being dropped.
