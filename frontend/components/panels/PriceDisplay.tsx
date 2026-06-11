"use client";
import { useEffect, useRef, useState } from "react";
import { formatPrice } from "@/utils/format";

interface Props {
  price: number;
  prevPrice?: number;
  decimals?: number;
  size?: "sm" | "md" | "lg" | "xl";
}

const SIZE_MAP = {
  sm: 13,
  md: 16,
  lg: 20,
  xl: 28,
};

export function PriceDisplay({
  price,
  prevPrice,
  decimals = 2,
  size = "md",
}: Props) {
  const [flashClass, setFlashClass] = useState("");
  const prevRef = useRef(prevPrice ?? price);

  useEffect(() => {
    const prev = prevRef.current;
    if (prev === price) return;
    setFlashClass(price > prev ? "price-flash-up" : "price-flash-down");
    const id = setTimeout(() => setFlashClass(""), 420);
    prevRef.current = price;
    return () => clearTimeout(id);
  }, [price]);

  const color =
    prevRef.current === price
      ? "#E8EDF5"
      : price > prevRef.current
        ? "#00E676"
        : "#FF3D5A";

  return (
    <span
      className={flashClass}
      style={{
        fontFamily: "var(--font-mono)",
        fontSize: SIZE_MAP[size],
        fontWeight: 700,
        color,
        letterSpacing: "0.02em",
        borderRadius: 2,
        padding: "1px 3px",
        transition: "color 0.2s ease",
        display: "inline-block",
      }}
    >
      {formatPrice(price, decimals)}
    </span>
  );
}
