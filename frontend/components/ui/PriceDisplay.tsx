"use client";

import { useEffect, useRef, useState } from "react";
import clsx from "clsx";

interface PriceDisplayProps {
  price: string;
  className?: string;
}

export function PriceDisplay({ price, className }: PriceDisplayProps) {
  const prevPriceRef = useRef(price);
  const [flash, setFlash] = useState<"bull" | "bear" | null>(null);

  useEffect(() => {
    if (price !== prevPriceRef.current) {
      const isUp = parseFloat(price) >= parseFloat(prevPriceRef.current);
      setFlash(isUp ? "bull" : "bear");

      const timer = setTimeout(() => setFlash(null), 180);
      prevPriceRef.current = price;

      return () => clearTimeout(timer);
    }
  }, [price]);

  return (
    <span
      className={clsx(
        "font-mono tabular-nums transition-colors duration-180",
        flash === "bull" && "bg-signal-bull/20 text-signal-bull rounded px-1",
        flash === "bear" && "bg-signal-bear/20 text-signal-bear rounded px-1",
        !flash && "text-text-primary",
        className,
      )}
    >
      {price}
    </span>
  );
}
