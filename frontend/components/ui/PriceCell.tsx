"use client";

import { usePriceFlash } from "@/hooks/usePriceFlash";
import clsx from "clsx";

interface PriceCellProps {
  value: string | number;
  prev?: string | number;
  isChange?: boolean; // If true, colors based on positive/negative sign of the value itself
  size?: "xs" | "sm" | "base" | "md" | "lg" | "xl" | "2xl" | "3xl" | "4xl";
  className?: string;
}

const sizeClasses = {
  xs: "text-xs",
  sm: "text-sm",
  base: "text-base",
  md: "text-md",
  lg: "text-lg",
  xl: "text-xl",
  "2xl": "text-2xl",
  "3xl": "text-3xl",
  "4xl": "text-4xl",
};

export function PriceCell({
  value,
  prev,
  isChange = false,
  size = "base",
  className,
}: PriceCellProps) {
  const flashClass = usePriceFlash(value);

  // Parse for comparisons
  const valFloat =
    typeof value === "string"
      ? parseFloat(value.replace(/[^0-9.-]/g, ""))
      : value;

  let textColor = "text-text-primary";

  if (isChange) {
    if (valFloat > 0) {
      textColor = "text-data-positive";
    } else if (valFloat < 0) {
      textColor = "text-data-negative";
    } else {
      textColor = "text-data-neutral";
    }
  } else if (prev !== undefined) {
    const prevFloat =
      typeof prev === "string"
        ? parseFloat(prev.replace(/[^0-9.-]/g, ""))
        : prev;
    if (!isNaN(valFloat) && !isNaN(prevFloat)) {
      if (valFloat > prevFloat) {
        textColor = "text-data-positive";
      } else if (valFloat < prevFloat) {
        textColor = "text-data-negative";
      } else {
        textColor = "text-data-neutral";
      }
    }
  }

  return (
    <span
      className={clsx(
        "font-data tabular-nums slashed-zero transition-colors duration-200 inline-block px-0.5 rounded-xs",
        sizeClasses[size],
        flashClass, // Applied flash class ('price-flash-up' or 'price-flash-down')
        !flashClass && textColor,
        className,
      )}
    >
      {value}
    </span>
  );
}
