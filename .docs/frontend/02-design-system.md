# 📖 Rule Book: Design System & UI Standards
## `.docs/frontend/02-design-system.md` — v2.0

> **Agent Load Instruction:** Load this file for ALL UI and styling work.
> Every component must conform to this design system. Deviations require
> explicit approval.

---

## 1. VISUAL IDENTITY

This is a **professional financial terminal**, not a consumer app.
The aesthetic is inspired by Bloomberg Terminal, Apple design language,
and modern quantitative trading interfaces.

### Core Principles
- **Dark mode is the only mode.** No light mode. No conditional themes.
- **Information density.** Every pixel earns its space with data.
- **Precision typography.** Numbers must be readable at a glance.
- **Motion with purpose.** Animations only when they convey state change.

---

## 2. COLOR SYSTEM (Tailwind v4 CSS Variables)

Define all colors as CSS variables in `app/globals.css`. Never use raw
hex codes in component files.

```css
/* app/globals.css */
@import "tailwindcss";

@layer base {
  :root {
    /* ── Backgrounds ── */
    --color-bg-base:        #0a0a0f;   /* Near-black canvas */
    --color-bg-surface:     #111118;   /* Cards, panels */
    --color-bg-elevated:    #1a1a24;   /* Dropdowns, modals */
    --color-bg-glass:       rgba(17, 17, 24, 0.72); /* Glassmorphism */

    /* ── Borders ── */
    --color-border-subtle:  rgba(255, 255, 255, 0.06);
    --color-border-default: rgba(255, 255, 255, 0.10);
    --color-border-strong:  rgba(255, 255, 255, 0.18);

    /* ── Text ── */
    --color-text-primary:   #f0f0f5;
    --color-text-secondary: #8b8b9e;
    --color-text-muted:     #4a4a5e;
    --color-text-inverse:   #0a0a0f;

    /* ── Accent (Trading Signals) ── */
    --color-signal-buy:     #00d4aa;   /* Green — execution / positive */
    --color-signal-sell:    #ff4d6d;   /* Red — risk / negative */
    --color-signal-neutral: #a78bfa;   /* Purple — neutral signal */
    --color-signal-warning: #f59e0b;   /* Amber — alert */

    /* ── Chart Colors ── */
    --color-chart-1:        #818cf8;
    --color-chart-2:        #34d399;
    --color-chart-3:        #f472b6;
    --color-chart-4:        #fb923c;

    /* ── Typography Scale ── */
    --font-sans:   "SF Pro Display", "Inter", system-ui, -apple-system, sans-serif;
    --font-mono:   "SF Mono", "JetBrains Mono", "Fira Code", monospace;
    --font-numeric: "SF Pro Display", "Tabular Nums", var(--font-sans);
  }
}
```

---

## 3. TYPOGRAPHY RULES

### Financial Numbers Must Use Tabular Numerals
```typescript
// All price/volume/percentage displays
<span className="font-mono tabular-nums text-text-primary">
  {price.toFixed(2)}
</span>

// Positive value
<span className="font-mono tabular-nums text-signal-buy">+2.34%</span>

// Negative value
<span className="font-mono tabular-nums text-signal-sell">-1.12%</span>
```

### Type Scale
| Usage | Class | Size |
|-------|-------|------|
| Page titles | `text-2xl font-semibold tracking-tight` | 24px |
| Section headers | `text-lg font-medium` | 18px |
| Body text | `text-sm` | 14px |
| Data labels | `text-xs text-text-secondary` | 12px |
| Ticker symbols | `text-sm font-mono font-semibold tracking-wide uppercase` | 14px |
| Price displays | `text-base font-mono tabular-nums` | 16px |

---

## 4. THE TOP NAVIGATION BAR

### Specification
- Floating element over page content (not pushes content down)
- Glassmorphism: `backdrop-blur-md` + `bg-bg-glass` + `border-b border-border-subtle`
- Apple-style horizontal layout: Logo left · Nav links center · Actions right
- Height: `h-14` (56px) — consistent across all screen sizes
- Position: `fixed top-0 left-0 right-0 z-50`

### Component Structure
```typescript
// components/navigation/TopNavigationBar.tsx
// THIS IS A SERVER COMPONENT — no 'use client' unless strictly necessary

import { NavigationItem } from "./NavigationItem";
import { useAuthToken } from "@/hooks/useAuthToken";  // Client sub-component

interface TopNavigationBarProps {
  items: NavigationItem[];
}

export function TopNavigationBar({ items }: TopNavigationBarProps) {
  return (
    <header
      className={[
        "fixed top-0 left-0 right-0 z-50",
        "h-14",
        "flex items-center justify-between px-6",
        "bg-bg-glass backdrop-blur-md",
        "border-b border-border-subtle",
      ].join(" ")}
      role="banner"
    >
      <NavigationLogo />
      <NavigationLinks items={items} />
      <NavigationActions />
    </header>
  );
}
```

### Glassmorphism Rules
```typescript
// The exact glassmorphism class combination — do not deviate
const glassmorphismClasses = [
  "bg-[rgba(17,17,24,0.72)]",   // Semi-transparent surface
  "backdrop-blur-md",            // Frosted glass blur
  "border border-border-subtle", // Subtle edge definition
  "shadow-lg",                   // Depth without heaviness
].join(" ");
```

---

## 5. COMPONENT DESIGN RULES

### Server Components by Default
```typescript
// CORRECT — Server Component (no directive needed)
export function TradingPanel() {
  return <div>...</div>;
}

// Only add 'use client' when the component:
// 1. Uses React hooks (useState, useEffect, etc.)
// 2. Attaches browser event listeners
// 3. Uses browser APIs (localStorage, etc.)
"use client";
export function LivePriceDisplay() {
  const [price, setPrice] = useState(0);
  // ...
}
```

### Component Size Limits
- Max **100 lines per component file** (excluding imports and types)
- If it exceeds this → extract to sub-components
- A component that handles both UI AND logic → extract logic to a hook

### Separation Pattern
```typescript
// hooks/useAuthToken.ts — logic isolated
"use client";
export function useAuthToken() {
  const [token, setToken] = useState<string | null>(null);
  // All token state logic here
  return { token, isAuthenticated: !!token };
}

// components/navigation/NavigationActions.tsx — pure UI
"use client";
import { useAuthToken } from "@/hooks/useAuthToken";

export function NavigationActions() {
  const { isAuthenticated } = useAuthToken();
  return (
    <div className="flex items-center gap-3">
      {isAuthenticated ? <UserMenu /> : <SignInButton />}
    </div>
  );
}
```

---

## 6. SPACING & LAYOUT TOKENS

Use only these spacing values for consistency:

| Token | Value | Usage |
|-------|-------|-------|
| `gap-1` | 4px | Tight icon groups |
| `gap-2` | 8px | Inline elements |
| `gap-3` | 12px | Button groups |
| `gap-4` | 16px | Section spacing |
| `gap-6` | 24px | Card padding |
| `gap-8` | 32px | Page sections |
| `p-4` / `px-6` | 16px / 24px | Container padding |

---

## 7. ABSOLUTELY FORBIDDEN

```typescript
// ❌ FORBIDDEN: Any light mode reference
className="dark:bg-gray-900 bg-white"  // Conditional light/dark

// ❌ FORBIDDEN: Hardcoded colors
style={{ color: "#f0f0f5" }}            // Use CSS variables

// ❌ FORBIDDEN: Inline styles for layout
style={{ display: "flex", gap: "16px" }} // Use Tailwind classes

// ❌ FORBIDDEN: Non-dark backgrounds
className="bg-white"
className="bg-gray-50"

// ❌ FORBIDDEN: Float values for prices
{parseFloat(price).toFixed(2)}  // Precision risk — use Decimal at API layer,
                                 // display as string from server

// ❌ FORBIDDEN: Untyped component props
export function Panel(props: any) { ... }

// ❌ FORBIDDEN: Emoji in financial UI
<span>📈 Price up!</span>   // Use proper icons (lucide-react) or indicators
```

---

## 8. ACCESSIBILITY BASELINE

Even a dark terminal UI must be accessible:

```typescript
// Semantic HTML over divs
<header>  // Not <div id="header">
<nav>     // Not <div className="nav">
<main>    // Not <div className="content">

// ARIA labels on icon-only buttons
<button aria-label="Close panel">
  <XIcon size={16} />
</button>

// Sufficient color contrast (WCAG AA minimum)
// text-text-secondary (#8b8b9e) on bg-bg-base (#0a0a0f) = 5.1:1 ✓
// text-text-muted (#4a4a5e) on bg-bg-base = 2.8:1 ⚠️ — use only for decorative text
```
