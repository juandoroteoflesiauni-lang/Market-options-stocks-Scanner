## Pull Request Checklist

> Complete all items before requesting review.
> The AI agent should verify all items before presenting code.

---

### Type of Change
- [ ] 🐛 Bug fix (non-breaking)
- [ ] ✨ New feature (within current phase scope)
- [ ] ♻️ Refactor (no behavior change)
- [ ] 🔒 Security fix
- [ ] 📝 Documentation update
- [ ] 🔧 Config / tooling change

---

### Backend (if applicable)
- [ ] All new functions have full type hints
- [ ] All new functions have Google-style docstrings
- [ ] No `print()` — logging module used
- [ ] No `except: pass` — specific exceptions with log
- [ ] No hardcoded constants — values in `config/`
- [ ] No network calls in Phase B or Phase C engines
- [ ] New models use `ConfigDict(frozen=True)`
- [ ] `data_lineage` is populated on all `MarketSnapshot` objects
- [ ] `black`, `isort`, `ruff`, `mypy` pass locally
- [ ] New code has tests with ≥ 80% coverage

### Frontend (if applicable)
- [ ] No `any` types used
- [ ] Server Component by default (no unnecessary `use client`)
- [ ] No inline styles — Tailwind classes only
- [ ] No hardcoded colors — CSS variables only
- [ ] No `console.log` (only `console.error` in catch blocks)
- [ ] `eslint`, `prettier`, `tsc` pass locally
- [ ] Phase scope respected (no Phase 2+ features in Phase 1)

### Security
- [ ] No secrets or API keys in code
- [ ] No `verify=False` in HTTP/WebSocket clients
- [ ] All external inputs validated with Pydantic
- [ ] `gitleaks` scan passes locally

---

### Linked Rule Books
List which rule books govern this change:
- [ ] `01-deep-funnel.md`
- [ ] `02-data-hub.md`
- [ ] `03-python-standards.md`
- [ ] `04-data-modeling.md`
- [ ] `05-async-event-engine.md`
- [ ] `frontend/01-scope.md`
- [ ] `frontend/02-design-system.md`
- [ ] `frontend/03-clean-code.md`
- [ ] `SECURITY.md`

---

### Summary
<!-- One paragraph describing what this PR does and why -->

### Testing Done
<!-- Describe manual and automated testing performed -->

### Architecture Impact
<!-- Does this change any module boundaries or data contracts? If yes, update ARCHITECTURE.md -->
