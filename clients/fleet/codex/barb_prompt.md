Use this prompt when the user invokes `/barb` or addresses `@barb`.

- For `/barb <message>`, send the message to rd-barb with the `rd-threads` MCP
  `send` tool (`to: "barb"`, `kind: "update"`, concise summary and full body).
- For `/barb chat [message]`, open or continue the rd-barb conversation using
  the same shared `send` tool. Read replies from the `rd-threads` `inbox` tool.
- Never contact a provider-specific Barb or use another transport. The shared
  tools route to the active rd-barb provider.
- Return the `correlation_id` as soon as `send` returns. Do not wait for a reply.
- Never read, include, print, or log credentials. The shared client owns auth.

Arguments: $ARGUMENTS
