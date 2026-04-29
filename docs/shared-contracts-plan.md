# [EPIC] Issue #157 — Shared Contracts Package Plan

**Status:** Draft  
**Scope:** Extract duplicated design-by-contract (DbC) precondition/postcondition validators into a standalone `robotics_contracts` package, consumable by `Pinocchio_Models`, `MuJoCo_Models`, and `OpenSim_Models`.  

---

## 1. Current State

All three biomechanics model repositories implement nearly identical **precondition** and **postcondition** validators under local `shared/contracts/` namespaces.  The duplication is structural, not merely cosmetic: each repo maintains its own copy of the same guard functions, tests, and exception semantics.

### 1.1 Pinocchio_Models
| Component | Location |
|-----------|----------|
| Generic shared package | `src/robotics_contracts/` (already exists) |
| Domain-specific wrappers | `src/pinocchio_models/shared/contracts/preconditions.py` |
| Domain-specific wrappers | `src/pinocchio_models/shared/contracts/postconditions.py` |
| Tests | `tests/unit/shared/test_postconditions.py` |
| Imports from | `robotics_contracts` (internal) + wraps with `URDFError` |

**Key observations**
- Already contains a **top-level `robotics_contracts`** package under `src/`, but it is bundled inside `pinocchio-models` wheel.
- Domain wrappers (`pinocchio_models.shared.contracts`) import from `robotics_contracts` at runtime and re-raise as `URDFError` with error codes (`PM101`–`PM111`).
- The generic `robotics_contracts` raises plain `ValueError`.

### 1.2 MuJoCo_Models
| Component | Location |
|-----------|----------|
| Local preconditions | `src/mujoco_models/shared/contracts/preconditions.py` |
| Local postconditions | `src/mujoco_models/shared/contracts/postconditions.py` |
| Tests | `tests/unit/shared/test_preconditions.py` |
| Exception type | `ValidationError` (extends `MuJoCoModelError`) |

**Key observations**
- Six public guards: `require_positive`, `require_non_negative`, `require_unit_vector`, `require_finite`, `require_in_range`, `require_shape`.
- Includes **Bolt optimizations** (`math.isfinite` fast-path, unrolled `math.sqrt` for unit-vector norm, scalar fast-paths).
- Raises `ValidationError` instead of `ValueError`.
- No dependency on any external `robotics_contracts` package.

### 1.3 OpenSim_Models
| Component | Location |
|-----------|----------|
| Local preconditions | `src/opensim_models/shared/contracts/preconditions.py` |
| Local postconditions | `src/opensim_models/shared/contracts/postconditions.py` |
| Tests | `tests/unit/shared/test_preconditions.py`, `tests/unit/test_edge_cases.py` |
| Exception type | `ValueError` |

**Key observations**
- Same six public guards as MuJoCo_Models.
- Contains the **most extensive Bolt optimizations** (fast-paths for `list`/`tuple`, `np.ndarray` dtype checks, `math.hypot` for 3-vectors).
- Raises plain `ValueError`.
- No dependency on any external `robotics_contracts` package.

### 1.4 Duplication Summary

| Function | Pinocchio | MuJoCo | OpenSim |
|----------|:---------:|:------:|:-------:|
| `require_positive` | ✅ | ✅ | ✅ |
| `require_non_negative` | ✅ | ✅ | ✅ |
| `require_unit_vector` | ✅ | ✅ | ✅ |
| `require_finite` | ✅ | ✅ | ✅ |
| `require_in_range` | ✅ | ✅ | ✅ |
| `require_shape` | ✅ | ✅ | ✅ |
| `ensure_positive_mass` | ✅ | — | — |
| `ensure_positive_definite_inertia` | ✅ | — | — |
| `require_valid_urdf_string` | (domain) | — | — |
| `ensure_valid_urdf` / `ensure_valid_urdf_tree` | (domain) | — | — |

**Pain points**
- **Three copies** of the same 6 precondition guards to maintain.
- **Divergent optimizations** — OpenSim has the fastest scalar paths; MuJoCo has unrolled vector math; Pinocchio is generic.
- **Different exception types** — `ValueError`, `ValidationError`, `URDFError` — making cross-repo portability harder.
- **Test duplication** — each repo tests the same edge cases independently.

---

## 2. Proposed Package Structure: `robotics_contracts`

### 2.1 Repository & Packaging

Create a **new standalone repository** `D-sorganization/robotics_contracts` (or publish as a namespace under an existing shared repo, e.g. `Tools` or a new monorepo slice).

```
robotics_contracts/
├── pyproject.toml
├── README.md
├── CHANGELOG.md
├── src/
│   └── robotics_contracts/
│       ├── __init__.py
│       ├── preconditions.py          # 6 generic guards
│       ├── postconditions.py         # 2 generic guards
│       └── _compat.py              # exception helpers
├── tests/
│   ├── test_preconditions.py
│   ├── test_postconditions.py
│   └── test_compat.py
└── docs/
    └── api.md
```

### 2.2 Module Design

#### `src/robotics_contracts/preconditions.py`

The consolidated module merges **the fastest implementation** from each repo:

| Function | Source of truth | Rationale |
|----------|-----------------|-----------|
| `require_positive` | OpenSim | Inline `math.isfinite`, no helper overhead |
| `require_non_negative` | OpenSim | Same as above |
| `require_unit_vector` | MuJoCo | Unrolled `math.sqrt` + `float()` cast, avoids `np.linalg.norm` |
| `require_finite` | OpenSim | Multi-tier fast-path (`float` → `list`/`tuple` → `np.ndarray` → fallback) |
| `require_in_range` | OpenSim | Inline `math.isfinite` for bounds |
| `require_shape` | OpenSim | `np.ndarray` shape check without `np.asarray` |

All functions raise **`ValueError`** (the lowest common denominator).  Consumers may wrap this in domain-specific exceptions.

```python
# Example consolidated signature
def require_positive(value: float, name: str) -> None:
    """Require *value* to be strictly positive.

    Raises:
        ValueError: If *value* is NaN, Inf, or <= 0.
    """
    if not math.isfinite(value):
        raise ValueError(f"{name} contains non-finite values")
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {value}")
```

#### `src/robotics_contracts/postconditions.py`

| Function | Source of truth |
|----------|-----------------|
| `ensure_positive_mass` | Pinocchio (generic) |
| `ensure_positive_definite_inertia` | Pinocchio (generic) |

#### `src/robotics_contracts/_compat.py` (optional)

Utility for consumers that need to map `ValueError` → domain exception:

```python
def map_exception(
    exc: ValueError,
    target_cls: type[Exception],
    error_code: str | None = None,
) -> Exception:
    """Map a robotics_contracts ValueError to a domain-specific exception."""
    ...
```

### 2.3 Dependencies

```toml
[project]
name = "robotics-contracts"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = [
    "numpy>=1.26.4",
]
```

No heavy simulation dependencies (no `pinocchio`, `mujoco`, `opensim`).  This keeps the package lightweight and CI-fast.

### 2.4 CI / CD

- **Lint:** `ruff` (same config as downstream repos: line-length 88, `py310` target).
- **Type check:** `mypy` with `disallow_untyped_defs = true`.
- **Test:** `pytest` with `pytest-benchmark` to guard against performance regressions.
- **Publish:** GitHub Actions → PyPI (or internal package index).

---

## 3. Migration Steps

### 3.1 Phase 0 — Create `robotics_contracts` Package (Week 1)

1. **Bootstrap repo** `D-sorganization/robotics_contracts`.
2. **Port code** from OpenSim/MuJoCo/Pinocchio into `src/robotics_contracts/`, selecting the fastest implementation for each guard.
3. **Port tests** — consolidate the best edge-case coverage from all three repos.
4. **Add benchmark baselines** — ensure `pytest-benchmark` captures current per-call latency; future PRs must not regress.
5. **Publish `v0.1.0`** to PyPI (or private index).

**Acceptance criteria**
- `pip install robotics-contracts` works.
- All 6 precondition + 2 postcondition functions present.
- 100% line coverage.
- Benchmarks record ≤ current fastest repo's latency for each guard.

---

### 3.2 Phase 1 — Pinocchio_Models Migration (Week 2)

Pinocchio already has `robotics_contracts` as an internal package; the migration is therefore a **lift-and-shift** plus dependency update.

| Step | Action |
|------|--------|
| 1 | Add `robotics-contracts>=0.1.0` to `pyproject.toml` dependencies. |
| 2 | Delete `src/robotics_contracts/` directory entirely. |
| 3 | Update domain wrappers (`pinocchio_models/shared/contracts/preconditions.py`) to import from external `robotics_contracts` instead of the deleted local copy. |
| 4 | Update `__init__.py` exports if any direct consumers reference `robotics_contracts`. |
| 5 | Run full test suite (`pytest -n auto`). |
| 6 | Verify no import errors in CI. |

**Backward compatibility**
- Domain wrappers remain in place; they continue to raise `URDFError`.  External consumers of `pinocchio_models.shared.contracts` see **no API change**.
- Internal `robotics_contracts.*` imports must be replaced with the PyPI package.

**Risk:** Low — the internal `robotics_contracts` was never exposed as a public entry-point in `pyproject.toml` scripts.

---

### 3.3 Phase 2 — MuJoCo_Models Migration (Week 3)

| Step | Action |
|------|--------|
| 1 | Add `robotics-contracts>=0.1.0` to `pyproject.toml` dependencies. |
| 2 | Replace `mujoco_models.shared.contracts.preconditions` with a **thin compatibility module** that imports from `robotics_contracts` and re-raises `ValidationError`. |
| 3 | Replace `mujoco_models.shared.contracts.postconditions` with domain-specific postconditions or delete if empty. |
| 4 | Update all internal imports (`from mujoco_models.shared.contracts.preconditions import ...`) — either keep the compatibility re-exports or switch to `robotics_contracts` directly. |
| 5 | Run full test suite. |

**Exception mapping pattern**

```python
# src/mujoco_models/shared/contracts/preconditions.py
from robotics_contracts.preconditions import (
    require_finite as _rc_require_finite,
    require_in_range as _rc_require_in_range,
    require_non_negative as _rc_require_non_negative,
    require_positive as _rc_require_positive,
    require_shape as _rc_require_shape,
    require_unit_vector as _rc_require_unit_vector,
)
from mujoco_models.exceptions import ValidationError

def require_positive(value: float, name: str) -> None:
    try:
        _rc_require_positive(value, name)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc

# ... repeat for remaining 5 guards
```

**Backward compatibility:** Public API of `mujoco_models.shared.contracts` is preserved; only internal implementation changes.

**Risk:** Medium — MuJoCo's `ValidationError` semantics must be preserved.  The compatibility wrapper guarantees this.

---

### 3.4 Phase 3 — OpenSim_Models Migration (Week 3–4)

| Step | Action |
|------|--------|
| 1 | Add `robotics-contracts>=0.1.0` to `pyproject.toml` dependencies. |
| 2 | Replace `opensim_models.shared.contracts.preconditions` with a **thin compatibility module** (same pattern as MuJoCo, but mapping to `ValueError` since OpenSim already uses `ValueError`). |
| 3 | Update all internal imports. |
| 4 | Run full test suite. |

**Note:** Because OpenSim already raises `ValueError`, the compatibility layer can be extremely thin — essentially a re-export:

```python
from robotics_contracts.preconditions import (
    require_finite,
    require_in_range,
    require_non_negative,
    require_positive,
    require_shape,
    require_unit_vector,
)
```

**Risk:** Low — exception type already matches the shared package.

---

### 3.5 Phase 4 — Cleanup & Deprecation (Week 4)

1. **Audit** remaining `shared/contracts/` directories in all three repos for any domain-specific guards that should **not** move to the shared package (e.g. `require_valid_urdf_string`, `ensure_valid_urdf_tree`).
2. **Document** in each repo's `README.md` or `CONTRIBUTING.md` that `robotics_contracts` is the canonical source for generic DbC guards.
3. **Archive** or mark obsolete any internal `robotics_contracts` documentation.

---

## 4. Timeline

| Week | Milestone |
|------|-----------|
| **1** | `robotics_contracts` repo created, `v0.1.0` published, CI green. |
| **2** | `Pinocchio_Models` migrated to external dependency; PR merged. |
| **3** | `MuJoCo_Models` migrated; PR merged. |
| **3–4** | `OpenSim_Models` migrated; PR merged. |
| **4** | Final cleanup, documentation update, issue #157 closed. |

---

## 5. Acceptance Criteria

### 5.1 For the new `robotics_contracts` package
- [ ] `pip install robotics-contracts` succeeds in a clean virtualenv.
- [ ] All 8 public functions (`require_*` × 6, `ensure_*` × 2) documented with docstrings.
- [ ] Unit tests achieve **100% line coverage**.
- [ ] Benchmarks show **no regression** vs. the fastest current implementation (OpenSim for most guards, MuJoCo for `require_unit_vector`).
- [ ] `mypy --strict` passes with no errors.

### 5.2 For each downstream repo
- [ ] `robotics-contracts` added to `pyproject.toml` dependencies.
- [ ] Local generic guard implementations removed (or converted to thin wrappers).
- [ ] Full `pytest` suite passes (unit + integration).
- [ ] CI pipeline green on the migration PR.
- [ ] No public API breakage (domain wrappers preserve exception types).

### 5.3 Cross-repo consistency
- [ ] All three repos consume the **same version** of `robotics_contracts` (or compatible semver range, e.g. `>=0.1.0,<1.0.0`).
- [ ] A bug fix in `robotics_contracts` can be released once and picked up by all three repos without individual patches.

---

## 6. Open Questions

1. **Namespace / repo name:** Should the package live in a new repo (`D-sorganization/robotics_contracts`) or within an existing shared repo (e.g. `Tools`)?  
   *Recommendation:* New repo for clean versioning and CI isolation.

2. **PyPI vs. internal index:** Is there an internal PyPI mirror or should we publish to public PyPI?  
   *Assumption:* Public PyPI under MIT license (consistent with all three downstream repos).

3. **Error code preservation:** Pinocchio's `URDFError` codes (`PM101`–`PM111`) are lost when calling `robotics_contracts` directly.  Should the shared package expose a `code` parameter or should domain wrappers add codes themselves?  
   *Recommendation:* Keep `robotics_contracts` simple (`ValueError` only).  Domain wrappers maintain their own error-code mapping.

4. **Additional shared guards:** Should domain-specific guards like `require_valid_urdf_string` or `ensure_valid_urdf_tree` eventually migrate to a `urdf_contracts` or `xml_contracts` package?  
   *Recommendation:* Out of scope for #157; evaluate separately if demand arises.

---

## 7. Related Files / References

| Repo | File | Relevance |
|------|------|-----------|
| Pinocchio_Models | `src/robotics_contracts/preconditions.py` | Source of generic guards (to be extracted) |
| Pinocchio_Models | `src/robotics_contracts/postconditions.py` | Source of generic postconditions |
| Pinocchio_Models | `src/pinocchio_models/shared/contracts/preconditions.py` | Domain wrapper (URDFError codes) |
| Pinocchio_Models | `src/pinocchio_models/shared/contracts/postconditions.py` | Domain wrapper (URDF validation) |
| MuJoCo_Models | `src/mujoco_models/shared/contracts/preconditions.py` | Independent implementation (ValidationError) |
| MuJoCo_Models | `src/mujoco_models/shared/contracts/postconditions.py` | Independent implementation |
| OpenSim_Models | `src/opensim_models/shared/contracts/preconditions.py` | Independent implementation (ValueError, Bolt-optimized) |
| OpenSim_Models | `src/opensim_models/shared/contracts/postconditions.py` | Independent implementation |

---

*End of plan*
