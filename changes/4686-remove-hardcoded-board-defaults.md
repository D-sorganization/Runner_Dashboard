---
issue: 4686
summary: "Route coordination boards only from RM config and fail closed when it is absent."
---

Remove the retired hardcoded board numbers from postman state load and board pagination. A saved cursor follows the configured issue. A missing `config/agent_communication.json` returns an explicit unavailable result and does not query GitHub.
