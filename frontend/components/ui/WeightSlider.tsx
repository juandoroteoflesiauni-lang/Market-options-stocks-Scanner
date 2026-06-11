"use client";
import * as SliderPrimitive from "@radix-ui/react-slider";

interface Props {
  label: string;
  value: number;
  min?: number;
  max?: number;
  step?: number;
  onChange: (value: number) => void;
}

export function WeightSlider({
  label,
  value,
  min = 0,
  max = 100,
  step = 1,
  onChange,
}: Props) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            color: "#8B9AAF",
            letterSpacing: "0.06em",
          }}
        >
          {label}
        </span>
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 11,
            fontWeight: 600,
            color: "#00C3FF",
            minWidth: 28,
            textAlign: "right",
          }}
        >
          {value}
        </span>
      </div>

      <SliderPrimitive.Root
        min={min}
        max={max}
        step={step}
        value={[value]}
        onValueChange={([v]) => onChange(v)}
        style={{
          position: "relative",
          display: "flex",
          alignItems: "center",
          width: "100%",
          height: 16,
          cursor: "pointer",
          userSelect: "none",
          touchAction: "none",
        }}
      >
        <SliderPrimitive.Track
          style={{
            position: "relative",
            flexGrow: 1,
            height: 3,
            background: "var(--bg-hover)",
            borderRadius: "var(--radius-pill)",
            overflow: "hidden",
          }}
        >
          <SliderPrimitive.Range
            style={{
              position: "absolute",
              height: "100%",
              background: "var(--grad-mid)",
            }}
          />
        </SliderPrimitive.Track>
        <SliderPrimitive.Thumb
          style={{
            display: "block",
            width: 12,
            height: 12,
            borderRadius: "50%",
            background: "#00C3FF",
            border: "2px solid #080C14",
            boxShadow: "0 0 6px rgba(0,195,255,0.5)",
            outline: "none",
            cursor: "grab",
            transition: "box-shadow 0.15s ease",
          }}
          onMouseEnter={(e) => {
            (e.target as HTMLElement).style.boxShadow =
              "0 0 10px rgba(0,195,255,0.8)";
          }}
          onMouseLeave={(e) => {
            (e.target as HTMLElement).style.boxShadow =
              "0 0 6px rgba(0,195,255,0.5)";
          }}
        />
      </SliderPrimitive.Root>
    </div>
  );
}
