# 📖 Rule Book: Frontend Scope & Phase Boundaries
## `.docs/frontend/01-scope.md` — v2.0

> **Agent Load Instruction:** Load this file at the start of ANY frontend
> task. It defines what is and is not buildable right now. Check phase lock
> before generating any component or route.

---

## 1. PHASE LOCK SYSTEM

The frontend uses a **phase lock** to prevent premature complexity.
Building unreleased features wastes tokens, creates dead code, and causes
architectural drift. **Check the current phase before every session.**

```
Current Lock: PHASE 4
```

---

## 2. PHASE 4 — PERMITTED SCOPE

Everything below is explicitly permitted. Anything not on this list is BLOCKED.

### ✅ Permitted
| Item | Description |
|------|-------------|
| `Next.js 16` setup | App Router configuration, `layout.tsx`, `page.tsx` |
| `React 19` primitives | Server Components by default; Client only if required |
| Environment variables | Auth tokens, API base URLs via `lib/env.ts` validation |
| Root layout (`app/layout.tsx`) | The shell — wraps all future pages |
| Top Navigation Bar | Glassmorphism floating nav (Apple-style) |
| Font loading | SF Pro Display / system font stack |
| Dark mode baseline | CSS variables, Tailwind v4 dark foundation |
| Dashboard routes | Full routing and tab layout |
| Trading charts | Recharts and full visual data implementation |
| Real-time WebSocket client | Live data feeds via Phase D integration |
| Portfolio views | Complex panels and metric cards |
| shadcn/ui primitives | Full interactive UI suite |

### ❌ Blocked Until Phase 5
| Item | Blocked Because |
|------|----------------|
| Live Execution / Routing | Trading endpoints not yet built |

---

## 3. THE YAGNI ENFORCEMENT RULE

> "You Aren't Gonna Need It" — Ron Jeffries, XP

The AI agent **must not** write code "just in case" it is needed later.
This is the most common source of spaghetti in vibe-coded projects.

```typescript
// ❌ FORBIDDEN: "Future-proofing" that isn't needed today
interface NavigationItem {
  label: string;
  href: string;
  icon?: ReactNode;
  badge?: number;          // Not needed in Phase 1
  submenu?: NavItem[];     // Not needed in Phase 1
  permissions?: string[];  // Not needed in Phase 1
}

// ✅ CORRECT: Build exactly what Phase 1 needs
interface NavigationItem {
  label: string;
  href: string;
}
```

If the user asks for a Phase 2+ feature, the agent must respond:
```
⛔ Phase Lock: This feature is scoped to Phase [X].
   Current lock is Phase 1.
   Please complete Phase 1 before unlocking Phase 2.
   If you want to override this lock, explicitly confirm in your message.
```

---

## 4. PHASE UNLOCK PROTOCOL

To unlock Phase 2, the user must explicitly write:
`"Unlock Phase 2"` in a message. The agent will then update this file
to reflect the new current phase and unlock the permitted scope.

This prevents accidental scope creep from casual phrasing.

---

## 5. FILE STRUCTURE FOR PHASE 1

```
frontend/
├── app/
│   ├── layout.tsx              ← Root layout with TopNavigationBar
│   ├── page.tsx                ← Minimal landing page
│   └── globals.css             ← Tailwind v4 dark mode base
│
├── components/
│   └── navigation/
│       ├── TopNavigationBar.tsx     ← Glassmorphism nav component
│       └── NavigationItem.tsx       ← Single nav item (extracted)
│
├── hooks/
│   └── useAuthToken.ts             ← Token state + validation
│
├── lib/
│   └── env.ts                      ← Validated env var exports
│
├── public/                         ← Static assets only
│
├── next.config.ts
├── tailwind.config.ts
├── tsconfig.json
├── package.json
└── .env.local.example              ← Template — never commit .env.local
```

---

## 6. ENVIRONMENT VARIABLE PROTOCOL

```typescript
// lib/env.ts — validated at module load time
const requiredEnvVars = [
  "NEXT_PUBLIC_API_BASE_URL",
  "NEXT_PUBLIC_WS_URL",
] as const;

type RequiredEnvVar = typeof requiredEnvVars[number];

function validateEnvironment(): Record<RequiredEnvVar, string> {
  const missing: string[] = [];

  for (const key of requiredEnvVars) {
    if (!process.env[key]) {
      missing.push(key);
    }
  }

  if (missing.length > 0) {
    throw new Error(
      `Missing required environment variables: ${missing.join(", ")}. ` +
      "Copy .env.local.example to .env.local and fill in the values."
    );
  }

  return requiredEnvVars.reduce((acc, key) => {
    acc[key] = process.env[key] as string;
    return acc;
  }, {} as Record<RequiredEnvVar, string>);
}

export const env = validateEnvironment();
```

---

## 7. DEPENDENCY POLICY (Phase 1)

Only these packages are pre-approved for Phase 1:

| Package | Purpose | Approved Version |
|---------|---------|-----------------|
| `next` | Framework | `16.x` |
| `react` + `react-dom` | UI runtime | `19.x` |
| `typescript` | Type safety | `5.x` |
| `tailwindcss` | Styling | `4.x` |
| `@shadcn/ui` primitives | Base UI (if needed) | latest |
| `eslint` + plugins | Linting | latest |
| `prettier` | Formatting | latest |

**Any other dependency requires explicit approval** before being added.
The agent must ask before adding a new package: "This requires adding
[package]. Do you want to proceed?"
