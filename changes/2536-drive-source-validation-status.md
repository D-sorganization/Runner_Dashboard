---
issue: 2536
summary: "Validate Drive folder references and distinguish configuration from reachability"
---

Drive connector registrations accept only supported canonical Google Drive folder references and reject malformed, conflicting, or lookalike URL inputs. A valid folder ID reports `configured`; it does not imply the connector is reachable or authenticated. Local folder availability continues to reflect mount access.
