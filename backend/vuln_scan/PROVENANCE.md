# Vendored Security-Scan Contract Validator

`contract.py` is a byte-identical copy of Repository_Management
`shared_scripts/oss_scanner_contract.py` (source of truth, RM#2386; epic
Runner_Dashboard#2566). The dashboard imports only this local copy, never a
sibling checkout, at runtime.

| File          | SHA-256 (LF line endings)                                          |
| ------------- | ------------------------------------------------------------------ |
| `contract.py` | `e3a4a439d7fe6b4c5d7351871f248753dd6f2e8aa6d8835c00ab2c22db391e79` |

To change the rules: edit the Repository_Management file first, merge it, copy it
here unchanged, and update the digest above and in
`tests/vuln_scan/test_vuln_scan_vendored.py`. `.ruff.toml` in this package keeps
Repository_Management's 88-column formatting so the copy stays byte-identical.
