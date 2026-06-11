"use client";

import { useRef, useEffect, useState } from "react";

/**
 * Hook to trigger a temporary green or red background flash when a financial value changes.
 * Supports both numbers and string values (e.g. "$194.50" or "45.2").
 */
export function usePriceFlash(value: number | string) {
  const prevValue = useRef(value);
  const [flashClass, setFlashClass] = useState<
    "price-flash-up" | "price-flash-down" | null
  >(null);

  useEffect(() => {
    if (value === prevValue.current) return;

    const curFloat =
      typeof value === "string"
        ? parseFloat(value.replace(/[^0-9.-]/g, ""))
        : value;
    const prevFloat =
      typeof prevValue.current === "string"
        ? parseFloat(prevValue.current.replace(/[^0-9.-]/g, ""))
        : prevValue.current;

    if (isNaN(curFloat) || isNaN(prevFloat) || curFloat === prevFloat) {
      prevValue.current = value;
      return;
    }

    const direction =
      curFloat > prevFloat ? "price-flash-up" : "price-flash-down";

    // Reset classes to trigger a reflow and restart keyframe animations
    setFlashClass(null);
    const frame = requestAnimationFrame(() => {
      setFlashClass(direction);
    });

    const timer = setTimeout(() => {
      setFlashClass(null);
    }, 400);

    prevValue.current = value;

    return () => {
      cancelAnimationFrame(frame);
      clearTimeout(timer);
    };
  }, [value]);

  return flashClass;
}
