# SKILL: Security & Authentication
## Compatible con: Antigravity, Claude Code, Cursor, VS Code

---

## DESCRIPCIÓN
Skill especializado para implementar seguridad, autenticación JWT y protección
de endpoints en la terminal de trading. Maneja dinero real — la seguridad es crítica.

---

## ACTIVACIÓN
Se activa cuando el contexto incluye:
- Archivos: `auth.py`, `security.py`, `jwt`, `login`, `token`
- Palabras clave: "autenticación", "login", "JWT", "password", "secrets"
- Imports de: `jose`, `bcrypt`, `passlib`, `HTTPBearer`

---

## REGLAS ABSOLUTAS DE SEGURIDAD

### Lo que JAMÁS debe aparecer en el código:
```
API_KEY = "abc123..."           ← PROHIBIDO
password = "mipassword"         ← PROHIBIDO
SECRET = "supersecret"          ← PROHIBIDO
jwt_secret = "hardcoded"        ← PROHIBIDO
```

### Lo que SIEMPRE debe aparecer:
```python
# De variables de entorno via settings
from core.config import settings
SECRET_KEY = settings.SECRET_KEY    ← CORRECTO
```

---

## STACK DE AUTENTICACIÓN OBLIGATORIO

```python
# Librerías requeridas (ya en requirements.txt):
# python-jose[cryptography]  → JWT creation/validation
# passlib[bcrypt]            → Password hashing
# slowapi                    → Rate limiting

# Flujo obligatorio:
# 1. POST /auth/login → verifica bcrypt hash → devuelve JWT (60 min) + refresh token
# 2. Todos los endpoints de trading → Depends(get_current_user) → valida JWT
# 3. POST /auth/refresh → valida refresh token → devuelve nuevo JWT
# 4. POST /auth/logout → invalida refresh token en Redis
```

---

## VALIDACIONES DE SEGURIDAD OBLIGATORIAS

```python
# Al registrar usuario:
# - Email único (índice en DB)
# - Password mínimo 8 chars, debe tener mayúscula, número y símbolo
# - Hash bcrypt SIEMPRE (nunca MD5/SHA1/plaintext)

# Al hacer login:
# - Rate limit: 5 intentos/minuto por IP
# - Bloqueo temporal después de 10 intentos fallidos
# - Log de cada intento (email, IP, éxito/fallo)

# En cada request autenticado:
# - Verificar firma JWT
# - Verificar no expirado
# - Verificar que el usuario aún está activo en DB
# - Log si el token está a punto de expirar (< 5 min)
```

---

## CHECKLIST DE SEGURIDAD

```
□ ¿Todos los secrets vienen de settings (variables de entorno)?
□ ¿Los passwords se hashean con bcrypt?
□ ¿Los endpoints de trading tienen Depends(get_current_user)?
□ ¿El rate limiting está configurado en /auth/login?
□ ¿Los logs de auth NO incluyen passwords?
□ ¿Los errores no exponen información sensible al cliente?
□ ¿CORS solo permite los orígenes del .env?
□ ¿El JWT tiene expiración configurada?
□ ¿Los refresh tokens se invalidan en logout?
```
