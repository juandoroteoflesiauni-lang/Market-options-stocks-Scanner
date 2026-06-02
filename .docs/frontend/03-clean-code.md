# 📖 Rule Book: Frontend Clean Code
## `.docs/frontend/03-clean-code.md` — v2.0

> **Agent Load Instruction:** Load this file for ALL TypeScript/React code
> generation and review. These standards are enforced by ESLint + Prettier
> in CI. Code that fails them is not merged.

---

## 1. TYPESCRIPT STANDARDS

### Strict Mode — Always On
```json
// tsconfig.json — these settings are mandatory
{
  "compilerOptions": {
    "strict": true,
    "noImplicitAny": true,
    "strictNullChecks": true,
    "noUncheckedIndexedAccess": true,
    "exactOptionalPropertyTypes": true
  }
}
```

### Interface Naming
```typescript
// CORRECT — descriptive, domain-specific names
interface NavigationItem {
  label: string;
  href: string;
}

interface MarketSignalDisplay {
  ticker: string;
  signalStrength: "CRITICAL" | "HIGH" | "MEDIUM" | "LOW";
  priceAtSignal: string;  // String from server — never float in UI
  emittedAtISO: string;
}

// FORBIDDEN — generic names
interface Data { ... }
interface NavItem { ... }   // Abbreviations
interface Props { ... }     // Ambiguous
```

### No `any` — Ever
```typescript
// FORBIDDEN
const handler = (event: any) => { ... }
const response: any = await fetch(...)

// CORRECT
const handler = (event: React.MouseEvent<HTMLButtonElement>) => { ... }
const response: ApiResponse<MarketSignalDisplay[]> = await fetchSignals()
```

---

## 2. REACT COMPONENT STANDARDS

### Single Responsibility — One Component, One Job
```typescript
// ❌ FORBIDDEN: Component doing too many things
export function TradingDashboard() {
  const [price, setPrice] = useState(0);
  const [signals, setSignals] = useState([]);
  const [portfolio, setPortfolio] = useState([]);
  // 80 lines of mixed state, business logic, and JSX
}

// ✅ CORRECT: Each component owns one concern
export function SignalList({ signals }: SignalListProps) {
  return (
    <ul>
      {signals.map((signal) => (
        <SignalItem key={signal.ticker} signal={signal} />
      ))}
    </ul>
  );
}
```

### Props: Always Typed, Never Optional Without Default
```typescript
// CORRECT — explicit interface, required props
interface SignalItemProps {
  signal: MarketSignalDisplay;
  onDismiss?: () => void;   // Optional is OK — but must have a default
}

export function SignalItem({ signal, onDismiss = () => {} }: SignalItemProps) {
  return (
    <li>
      <span className="font-mono uppercase">{signal.ticker}</span>
      <SignalStrengthBadge strength={signal.signalStrength} />
    </li>
  );
}
```

### Component File Size Limits
- **Max 100 lines per file** (excluding imports and type definitions)
- If exceeded: extract sub-components or extract logic to a hook
- One component per file (default export allowed for pages, named for components)

---

## 3. HOOKS STANDARDS

### Hooks Contain Logic — Components Contain UI
```typescript
// hooks/useMarketSignals.ts — logic lives here
"use client";
import { useState, useEffect } from "react";

interface UseMarketSignalsReturn {
  signals: MarketSignalDisplay[];
  isLoading: boolean;
  error: string | null;
}

export function useMarketSignals(): UseMarketSignalsReturn {
  const [signals, setSignals] = useState<MarketSignalDisplay[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let isMounted = true;
    setIsLoading(true);

    fetchSignals()
      .then((data) => {
        if (isMounted) {
          setSignals(data);
          setIsLoading(false);
        }
      })
      .catch((err: Error) => {
        if (isMounted) {
          setError(err.message);
          setIsLoading(false);
        }
      });

    return () => {
      isMounted = false;  // Cleanup prevents setState on unmounted component
    };
  }, []);

  return { signals, isLoading, error };
}
```

### Hook Rules
- Hooks must start with `use` prefix
- Each hook does ONE thing (not `useEverything`)
- Side effects have cleanup functions
- No nested hooks

---

## 4. ERROR HANDLING IN UI

### Never Swallow Errors Silently
```typescript
// FORBIDDEN
try {
  await saveData();
} catch {
  // silent failure
}

// CORRECT
try {
  await saveData();
} catch (error) {
  const message = error instanceof Error ? error.message : "Unknown error";
  console.error("Failed to save data:", message);
  setError(message);  // Show to user if appropriate
}
```

### Error Boundaries for Critical Sections
```typescript
// components/ErrorBoundary.tsx
"use client";
import { Component, ReactNode } from "react";

interface ErrorBoundaryState {
  hasError: boolean;
  errorMessage: string;
}

export class ErrorBoundary extends Component<
  { children: ReactNode; fallback?: ReactNode },
  ErrorBoundaryState
> {
  state: ErrorBoundaryState = { hasError: false, errorMessage: "" };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, errorMessage: error.message };
  }

  render() {
    if (this.state.hasError) {
      return this.props.fallback ?? (
        <div className="text-signal-sell text-sm p-4">
          Component error: {this.state.errorMessage}
        </div>
      );
    }
    return this.props.children;
  }
}
```

---

## 5. NAMING CONVENTIONS

| Category | Convention | Example |
|----------|-----------|---------|
| Components | `PascalCase` | `TopNavigationBar`, `SignalItem` |
| Hooks | `camelCase` with `use` prefix | `useAuthToken`, `useMarketSignals` |
| Utilities | `camelCase` | `formatPrice`, `parseSignalDate` |
| Types/Interfaces | `PascalCase` | `NavigationItem`, `MarketSignalDisplay` |
| Constants | `SCREAMING_SNAKE_CASE` | `MAX_SIGNALS_DISPLAYED` |
| CSS variables | `--color-kebab-case` | `--color-signal-buy` |
| Files | `PascalCase` for components, `camelCase` for hooks/utils | `TopNavigationBar.tsx`, `useAuthToken.ts` |
| Route folders | `kebab-case` | `app/trading-signals/page.tsx` |

---

## 6. IMPORT ORDER (Enforced by ESLint `import/order`)

```typescript
// 1. React and Next.js
import { useState, useEffect } from "react";
import Link from "next/link";
import type { Metadata } from "next";

// 2. Third-party libraries
import { ChevronDownIcon } from "lucide-react";

// 3. Internal — absolute paths only (no `../../`)
import { TopNavigationBar } from "@/components/navigation/TopNavigationBar";
import { useAuthToken } from "@/hooks/useAuthToken";
import { env } from "@/lib/env";

// 4. Types (last, using `import type`)
import type { NavigationItem } from "@/components/navigation/TopNavigationBar";
```

---

## 7. ESLINT CONFIGURATION

```json
// .eslintrc.json
{
  "extends": [
    "next/core-web-vitals",
    "plugin:@typescript-eslint/strict-type-checked",
    "plugin:import/recommended",
    "plugin:import/typescript",
    "prettier"
  ],
  "rules": {
    "@typescript-eslint/no-explicit-any": "error",
    "@typescript-eslint/no-unused-vars": "error",
    "@typescript-eslint/consistent-type-imports": "error",
    "import/order": ["error", { "newlines-between": "always" }],
    "no-console": ["warn", { "allow": ["error"] }],
    "prefer-const": "error",
    "no-var": "error"
  }
}
```

---

## 8. PRETTIER CONFIGURATION

```json
// prettier.config.js
module.exports = {
  semi: true,
  singleQuote: false,
  trailingComma: "all",
  printWidth: 100,
  tabWidth: 2,
  plugins: ["prettier-plugin-tailwindcss"],
  tailwindConfig: "./tailwind.config.ts"
};
```

---

## 9. COMMON VIOLATIONS CHECKLIST

Before presenting any code, the agent must verify:

```
[ ] No `any` types
[ ] All component props are typed with interfaces
[ ] No inline styles (use Tailwind classes)
[ ] No raw hex colors in className (use CSS variables)
[ ] No console.log (use console.error only for caught errors)
[ ] Single-responsibility per component and hook
[ ] Server Component by default (no 'use client' unless needed)
[ ] Error cases are handled and surfaced to the user
[ ] Cleanup in useEffect (return cleanup function)
[ ] Import order follows the 4-group convention above
```
