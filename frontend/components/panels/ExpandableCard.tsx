"use client";
import { useState, type ReactNode } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { ChevronDown } from "lucide-react";

interface Props {
  title: string;
  subtitle?: string;
  children: ReactNode;
  defaultOpen?: boolean;
  accentColor?: string;
  headerRight?: ReactNode;
}

export function ExpandableCard({
  title,
  subtitle,
  children,
  defaultOpen = false,
  accentColor = "#00C3FF",
  headerRight,
}: Props) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <div
      style={{
        background: "var(--bg-panel)",
        border: `1px solid ${open ? `${accentColor}30` : "rgba(255,255,255,0.06)"}`,
        borderRadius: "var(--radius-lg)",
        overflow: "hidden",
        transition: "border-color 0.2s ease",
      }}
    >
      {/* Header */}
      <button
        onClick={() => setOpen((o) => !o)}
        style={{
          width: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "12px 16px",
          background: open ? `${accentColor}06` : "transparent",
          border: "none",
          cursor: "pointer",
          transition: "background 0.2s ease",
        }}
      >
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "flex-start",
            gap: 2,
          }}
        >
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              fontWeight: 600,
              color: open ? accentColor : "#E8EDF5",
              letterSpacing: "0.1em",
              textTransform: "uppercase",
              transition: "color 0.2s ease",
            }}
          >
            {title}
          </span>
          {subtitle && (
            <span
              style={{
                fontFamily: "var(--font-ui)",
                fontSize: 11,
                color: "#4A5568",
              }}
            >
              {subtitle}
            </span>
          )}
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {headerRight}
          <motion.span
            animate={{ rotate: open ? 180 : 0 }}
            transition={{ duration: 0.2 }}
            style={{ display: "flex", color: open ? accentColor : "#4A5568" }}
          >
            <ChevronDown size={14} />
          </motion.span>
        </div>
      </button>

      {/* Animated body */}
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            key="body"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
            style={{ overflow: "hidden" }}
          >
            <div
              style={{
                padding: "0 16px 16px",
                borderTop: `1px solid rgba(255,255,255,0.05)`,
              }}
            >
              {children}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
