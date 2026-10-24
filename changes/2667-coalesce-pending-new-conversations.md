---
issue: 2667
summary: "Coalesce repeated pending New conversation activations within one console while preserving retry and independent consoles."
---

- Repeated New conversation activations share one pending creation in the same console. Failures preserve the current conversation and permit retry; subsequent deliberate creations and independent consoles stay separate.
