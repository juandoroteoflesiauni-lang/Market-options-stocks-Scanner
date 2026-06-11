"use client";
import { useState } from "react";
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
  const [lastSeenPrice, setLastSeenPrice] = useState(price);

  if (lastSeenPrice !== price) {
    const direction = price > lastSeenPrice ? "up" : "down";
    setLastSeenPrice(price);
    setFlashClass(
      direction === "up" ? "price-flash-up" : "price-flash-down",
    );
    setTimeout(() => setFlashClass(""), 420);
  }

  const baseline = prevPrice ?? lastSeenPrice;
  const color =
    baseline === price
      ? "#E8EDF5"
      : price > baseline
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
