---
issue: 4274
summary: "Security (WP19 F-07): request body limit is enforced on the bytes actually received (chunked / understated Content-Length, any method), ignores the client X-Max-Body-Size header, and applies @limit_body_size route caps; one MaxBodySizeMiddleware replaces the two header-only checks"
---
