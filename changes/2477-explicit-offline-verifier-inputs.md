---
issue: 2477
summary: "Allow offline citation verification with explicit local database and knowledge-pack inputs; keep supplied SQLite evidence read-only and disable fleet peer lookups when requested."
dl_state: "in_review"
next_step: "Review the fail-closed input contract, then merge through the required checks."
branch: "codex/2477-explicit-library-verifier"
paths: "backend/library/__main__.py,backend/library/verify.py,backend/library/codebase_refs.py,backend/library/retrieval_ledger.py,backend/library/codebase_retrieval_ledger.py,tests/library/test_codebase_refs.py,tests/library/test_standalone_verify_inputs.py,tests/library/test_verify.py,tests/library/test_retrieval_ledger.py,tests/library/test_codebase_retrieval_ledger.py,tests/api/test_library_codebase_verify.py"
---
