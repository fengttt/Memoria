---
inclusion: always
---

# Coding Standards

## Lint & Type Checks

All code changes must pass without errors:

```bash
make check   # runs: ruff check + ruff format --check + mypy
```

Or individually:
```bash
ruff check memoria/ tests/
mypy memoria/
```

**Before finishing any code task:**
- No `ruff` errors (F-rules, E-rules, etc.)
- No unused variables (`F841`) — remove or use `_` prefix
- No `mypy` attribute errors — if adding fields to a dataclass used across the codebase, add them to all relevant types (e.g., both `GovernanceCycleResult` and `GovernanceReport`)
- No untyped or missing imports that mypy flags

## Key patterns

- New fields on `GovernanceCycleResult` (tabular internal) must also be added to `GovernanceReport` (public interface in `interfaces.py`) if the scheduler or service layer reads them
- Use `getattr(obj, "field", default)` only as a last resort — prefer keeping types in sync
