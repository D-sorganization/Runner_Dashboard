---
issue: 4417
summary: "A node no longer relays a hub error answer as HTTP 200: request errors keep their status and detail, a rejected fleet credential and hub 5xx become 502."
paths: "backend/proxy_utils.py,tests/api/test_hub_proxy_error_status_4417.py"
---
