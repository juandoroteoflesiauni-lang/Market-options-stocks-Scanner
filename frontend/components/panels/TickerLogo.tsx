"use client";
import { memo, useMemo, useState } from "react";

interface Props {
  symbol: string;
  size?: number;
  rounded?: boolean;
}

// Stable color from symbol for the fallback initial badge
function hashColor(symbol: string): string {
  const palette = [
    "#1f4e6b",
    "#3a4a6b",
    "#5b4a6b",
    "#6b4a5b",
    "#6b5b4a",
    "#4a6b5b",
    "#4a5b6b",
    "#5b6b4a",
  ];
  let h = 0;
  for (let i = 0; i < symbol.length; i++)
    h = (h * 31 + symbol.charCodeAt(i)) | 0;
  return palette[Math.abs(h) % palette.length];
}

// Known crypto base symbols — used to route to a crypto logo CDN
const CRYPTO_BASES = new Set([
  "BTC",
  "ETH",
  "BNB",
  "SOL",
  "XRP",
  "ADA",
  "DOGE",
  "TRX",
  "TON",
  "AVAX",
  "LINK",
  "MATIC",
  "DOT",
  "LTC",
  "BCH",
  "SHIB",
  "UNI",
  "ATOM",
  "XLM",
  "ETC",
  "FIL",
  "APT",
  "ARB",
  "OP",
  "NEAR",
  "HBAR",
  "VET",
  "INJ",
  "SUI",
  "SEI",
  "PEPE",
  "WIF",
  "RNDR",
  "FTM",
  "AAVE",
  "MKR",
  "GRT",
  "SAND",
  "MANA",
  "AXS",
]);

function parseSymbol(raw: string): { base: string; kind: "crypto" | "stock" } {
  const s = raw.toUpperCase().trim();
  // Strip common quote suffixes for crypto pairs
  const stripped = s
    .replace(/[-_/](USDT|USDC|BUSD|USD|BTC|ETH|EUR)$/i, "")
    .replace(/(USDT|USDC|BUSD)$/i, (m) => (s.endsWith(m) ? "" : m));
  const base = stripped || s;
  const kind =
    CRYPTO_BASES.has(base) || /[-_/](USDT|USDC|BUSD|USD)$/i.test(s)
      ? "crypto"
      : "stock";
  return { base, kind };
}

function buildSources(symbol: string): string[] {
  const { base, kind } = parseSymbol(symbol);
  if (kind === "crypto") {
    return [
      `https://assets.coincap.io/assets/icons/${base.toLowerCase()}@2x.png`,
      `https://cryptologos.cc/logos/${base.toLowerCase()}-${base.toLowerCase()}-logo.png`,
    ];
  }
  return [
    `https://financialmodelingprep.com/image-stock/${base}.png`,
    `https://assets.parqet.com/logos/symbol/${base}?format=png`,
  ];
}

export const TickerLogo = memo(function TickerLogo({
  symbol,
  size = 18,
  rounded = true,
}: Props) {
  const sources = useMemo(() => buildSources(symbol), [symbol]);
  const [idx, setIdx] = useState(0);
  const failed = idx >= sources.length;

  const dim = { width: size, height: size };
  const radius = rounded ? "50%" : "var(--radius-sm)";

  if (failed) {
    const { base } = parseSymbol(symbol);
    return (
      <span
        aria-label={`${symbol} logo`}
        style={{
          ...dim,
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          borderRadius: radius,
          background: hashColor(base),
          color: "#E8EDF5",
          fontFamily: "var(--font-mono)",
          fontSize: Math.max(8, Math.floor(size * 0.46)),
          fontWeight: 700,
          letterSpacing: "-0.02em",
          flexShrink: 0,
          border: "1px solid rgba(255,255,255,0.08)",
        }}
      >
        {base.slice(0, 2)}
      </span>
    );
  }

  return (
    <img
      src={sources[idx]}
      alt={`${symbol} logo`}
      onError={() => setIdx((i) => i + 1)}
      style={{
        ...dim,
        borderRadius: radius,
        objectFit: "contain",
        background: "#0f1419",
        border: "1px solid rgba(255,255,255,0.06)",
        flexShrink: 0,
      }}
    />
  );
});
