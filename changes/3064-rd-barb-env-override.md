---
issue: 3064
summary: "rd-barb config: non-empty RD_BARB_* environment values win over rd-barb.env"
---

`rd_barb_notify.load_config` overlaid only the four webhook keys, so `RD_BARB_PROVIDER`, `RD_BARB_MODEL`, `RD_BARB_FAILOVER` and `RD_BARB_LAUNCH_CMD` set in the environment were ignored by `rd_barb_provider.py`. Every non-empty `RD_BARB_*` key now wins over the file; `RD_BARB_CONFIG` (the file path) is not copied.
