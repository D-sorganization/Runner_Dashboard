---
issue: 4393
summary: "Repair invalid change fragments and validate the whole changes tree"
---

Slice 1 of #4393. Invalid fragments already on main make the changes-fragment hook reject later pushes whose range includes them. This slice repairs those fragments, drops misnamed duplicates that already have a valid twin, and adds a tree-wide validation test. Prettier and mypy on main stay open for a later slice.
