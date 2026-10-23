---
issue: 2427
summary: "Send the browser request marker on silent refresh and show sign-out failures honestly."
---

## Changed

- Silent session refresh sends `X-Requested-With: XMLHttpRequest`, including when called outside the normal API client.
- Sign-out calls the success handler only after a successful response. HTTP and network failures keep the current session state and show an accessible error toast with a retry instruction.

## Validation

- Five initial regression failures covered the refresh marker, HTTP/network failures and rendered shell feedback. Successful sign-out remains covered.
