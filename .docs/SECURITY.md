# 📖 Security Rules
## `.docs/SECURITY.md` — v2.0

> **Agent Load Instruction:** Load this file whenever the task involves
> authentication, API keys, data validation, network calls, dependencies,
> or anything that touches external systems.

---

## 1. SECURITY POSTURE

This is a financial system. A security breach has direct monetary consequences.
Security is not a feature to add later — it is a constraint active from line 1.

**Threat model:** External API key theft · Data injection via untrusted market data ·
Dependency supply-chain attacks · Secrets in git history · Unvalidated inputs.

---

## 2. SECRET MANAGEMENT (Zero-Tolerance)

### Rules
```
RULE SEC-1: Zero secrets in source code. EVER.
RULE SEC-2: Zero secrets in .env files committed to git.
RULE SEC-3: All secrets load via environment variables at runtime.
RULE SEC-4: pydantic-settings validates secret format at startup.
RULE SEC-5: SecretStr type used for all API keys (prevents accidental logging).
```

### Implementation
```python
# CORRECT — pydantic SecretStr never logs the value
from pydantic import SecretStr
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    fmp_api_key: SecretStr    # repr() shows "**********", not the actual key
    
# CORRECT — accessing the value explicitly when needed
key_value: str = settings.fmp_api_key.get_secret_value()

# FORBIDDEN — raw string for secrets
API_KEY = "sk-abc123"           # Hardcoded — CRITICAL violation
API_KEY = os.getenv("API_KEY")  # Unvalidated — may be None, empty, or wrong format
```

### `.gitignore` (Mandatory)
```
# Secrets and environment files — NEVER commit these
.env
.env.local
.env.*.local
*.pem
*.key
*.p12
secrets/
```

---

## 3. INPUT VALIDATION (All External Data)

Every piece of data entering the system from an external source must
be validated before use. "External" means: APIs, WebSockets, user input,
environment variables, config files.

```python
# RULE SEC-6: Validate ALL external input at the boundary (Phase A / MarketDataHub)
# Pydantic v2 enforces this automatically — use it.

# FORBIDDEN: Using raw API response without validation
raw = await api.get("/quote/AAPL")
price = raw["price"]   # KeyError waiting to happen. What if price is negative?

# CORRECT: Let Pydantic validate and raise if invalid
try:
    snapshot = fmp_normalizer.normalize(raw, ingestion_start_ns=time.time_ns())
except (ValidationError, KeyError) as exc:
    logger.warning("Discarding invalid data from FMP", extra={"error": str(exc)})
    return Result.failure(reason=str(exc))
```

---

## 4. DEPENDENCY SECURITY

### Automated Scanning (CI)
```yaml
# .github/workflows/security.yml
- name: Audit Python dependencies
  run: pip-audit --strict

- name: Scan for secrets in code
  uses: gitleaks/gitleaks-action@v2

- name: SAST scan (Python)
  run: bandit -r backend/ -ll -ii  # Level HIGH, confidence HIGH
  
- name: Audit npm dependencies
  run: npm audit --audit-level=moderate
```

### Dependency Rules
- **Pin all production dependencies** to exact versions in `pyproject.toml` and `package.json`
- **Review changelogs** before updating any dependency
- **Never use `*` or `latest` as a version** in production dependencies
- **Weekly automated Dependabot PRs** for security patches

```toml
# pyproject.toml — pin exact versions
[tool.poetry.dependencies]
python = "^3.12"
pydantic = "2.7.1"        # Pinned — not "^2.7"
httpx = "0.27.0"          # Pinned
pydantic-settings = "2.3.4"
```

---

## 5. NETWORK SECURITY

```python
# RULE SEC-7: Always validate SSL certificates in production
import httpx

# FORBIDDEN in production
client = httpx.AsyncClient(verify=False)   # SSL disabled

# CORRECT
client = httpx.AsyncClient(
    verify=True,           # SSL validation on (default)
    timeout=httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0),
    follow_redirects=False,   # Explicit — prevent redirect attacks
)

# RULE SEC-8: Always set timeouts — never let requests hang forever
```

### WebSocket Security
```python
# CORRECT — validate WebSocket URL from environment, use SSL
ws_url: str = settings.massive_ws_url  # From validated pydantic-settings
assert ws_url.startswith("wss://"), "WebSocket must use WSS (SSL)"

async with websockets.connect(
    ws_url,
    ssl=True,
    extra_headers={"Authorization": f"Bearer {key}"},
    ping_interval=20,       # Keep-alive
    ping_timeout=10,
    close_timeout=10,
) as websocket:
    ...
```

---

## 6. LOGGING SECURITY

```python
# RULE SEC-9: Never log sensitive values
# pydantic SecretStr prevents accidental logging via repr()

# FORBIDDEN — logs the actual API key
logger.info("Using key: %s", api_key)

# CORRECT — logs that a key is present, not its value
logger.info("API key loaded: %s chars", len(api_key.get_secret_value()))

# FORBIDDEN — logs raw user-supplied data (XSS in log viewers)
logger.info("Received ticker: %s", user_input)

# CORRECT — sanitize before logging
safe_ticker = re.sub(r"[^A-Z0-9.]", "", user_input.upper())[:10]
logger.info("Processing ticker: %s", safe_ticker)
```

---

## 7. FRONTEND SECURITY

```typescript
// RULE SEC-10: No secrets in NEXT_PUBLIC_ variables
// NEXT_PUBLIC_ variables are exposed to the browser — never put API keys there

// FORBIDDEN
NEXT_PUBLIC_FMP_API_KEY=abc123   // Exposed to every browser visitor

// CORRECT: API keys stay server-side only
FMP_API_KEY=abc123               // Server-side only, never in NEXT_PUBLIC_

// RULE SEC-11: Sanitize all server-side data before rendering
// (Next.js handles most XSS via JSX, but be explicit with dangerouslySetInnerHTML)

// FORBIDDEN
<div dangerouslySetInnerHTML={{ __html: userContent }} />

// CORRECT (only if HTML is absolutely required)
import DOMPurify from "dompurify";
<div dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(userContent) }} />
```

---

## 8. SECURITY INCIDENT CHECKLIST

If a potential security issue is found during code review:

```
[ ] STOP — do not commit the code
[ ] Classify: Critical / High / Medium / Low
[ ] If Critical (exposed secret, auth bypass): HALT all merges
[ ] Document in a private issue (not public)
[ ] Fix in an isolated branch
[ ] Run full security scan suite after fix
[ ] Rotate any potentially exposed credentials
[ ] Add regression test to prevent recurrence
```

---

## 9. BANDIT CONFIGURATION

```toml
# pyproject.toml
[tool.bandit]
exclude_dirs = ["tests", ".venv"]
skips = []  # No skips in this project
tests = [
  "B101",   # assert_used — warn on assert in non-test code
  "B105",   # hardcoded_password_string
  "B106",   # hardcoded_password_funcarg
  "B108",   # probable_insecure_usage
  "B201",   # flask_debug_true (unlikely but defensive)
  "B301",   # pickle — forbidden
  "B324",   # MD5/SHA1 — forbidden
  "B501",   # ssl_with_bad_version
  "B506",   # yaml_load — use yaml.safe_load
]
```
