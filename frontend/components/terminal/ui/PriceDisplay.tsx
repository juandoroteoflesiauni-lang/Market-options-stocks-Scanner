"use client";

import { memo, useEffect, useRef, useState } from "react";

import { cn } from "@/lib/terminal/format";

/**
 * Live-updating price with a green/red background flash on tick.
 */
export const PriceDisplay = memo(function PriceDisplay({
  price,
  className,
  size = "md",
  prefix = "$",
  digits = 2,
}: {
  price: number;
  className?: string;
  size?: "sm" | "md" | "lg" | "xl";
  prefix?: string;
  digits?: number;
}) {
  const [flash, setFlash] = useState<"" | "flash-up" | "flash-down">("");
  const prevRef = useRef(price);

  useEffect(() => {
    if (price > prevRef.current) setFlash("flash-up");
    else if (price < prevRef.current) setFlash("flash-down");
    prevRef.current = price;
    const id = setTimeout(() => setFlash(""), 500);
    return () => clearTimeout(id);
  }, [price]);

  const sizes: Record<string, string> = {
    sm: "text-sm",
    md: "text-base",
    lg: "text-2xl",
    xl: "text-3xl",
  };

  return (
    <span
      className={cn(
        "inline-block rounded px-1 font-mono font-semibold tabular-nums text-text-primary",
        sizes[size],
        flash,
        className,
      )}
    >
      {prefix}
      {price.toLocaleString("en-US", {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      })}
    </span>
  );
});
