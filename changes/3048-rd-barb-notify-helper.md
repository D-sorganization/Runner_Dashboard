---
issue: 3048
summary: "rd-barb notify helper: validated question/alert/fyi envelopes to the Grok Barb webhook, dedupe, fyi digest, fail closed"
---

Messages from the persistent rd-barb seat reach Dieter only through Grok Barb. Questions carry exactly one recommended option and a one-line reason; alerts carry a recommended action.
