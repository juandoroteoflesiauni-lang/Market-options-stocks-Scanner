"use client"

import { useMemo, useState } from "react"
import { cn } from "@/lib/terminal/format"
import type { Candle } from "@/lib/terminal/types"

interface CandleChartProps {
  candles: Candle[]
  height?: number
  className?: string
  showVolume?: boolean
}

export function CandleChart({ candles, height = 320, className, showVolume = true }: CandleChartProps) {
  const [hover, setHover] = useState<number | null>(null)
  const width = 1000
  const volH = showVolume ? 60 : 0
  const priceH = height - volH - 8
  const padR = 56

  const { max, min, vMax } = useMemo(() => {
    const highs = candles.map((c) => c.high)
    const lows = candles.map((c) => c.low)
    const vols = candles.map((c) => c.volume)
    return {
      max: Math.max(...highs),
      min: Math.min(...lows),
      vMax: Math.max(...vols),
    }
  }, [candles])

  if (!candles.length) return <div style={{ height }} className={cn("rounded-lg bg-bg-panel", className)} />

  const range = max - min || 1
  const plotW = width - padR
  const step = plotW / candles.length
  const cw = Math.max(1.5, step * 0.62)

  const yFor = (p: number) => priceH - ((p - min) / range) * priceH
  const gridLines = 5
  const active = hover != null ? candles[hover] : candles[candles.length - 1]

  return (
    <div className={cn("relative", className)}>
      <svg viewBox={`0 0 ${width} ${height}`} className="w-full" style={{ height }} preserveAspectRatio="none"
        onMouseLeave={() => setHover(null)}
        onMouseMove={(e) => {
          const rect = (e.currentTarget as SVGSVGElement).getBoundingClientRect()
          const x = ((e.clientX - rect.left) / rect.width) * width
          const idx = Math.floor(x / step)
          if (idx >= 0 && idx < candles.length) setHover(idx)
        }}
      >
        {/* horizontal grid + price axis */}
        {Array.from({ length: gridLines + 1 }).map((_, i) => {
          const y = (priceH / gridLines) * i
          const price = max - (range / gridLines) * i
          return (
            <g key={i}>
              <line x1={0} y1={y} x2={plotW} y2={y} stroke="var(--color-border-subtle)" strokeWidth={0.5} />
              <text x={plotW + 6} y={y + 3} fontSize={9} fill="var(--color-text-muted)" fontFamily="var(--font-mono)">
                {price.toFixed(2)}
              </text>
            </g>
          )
        })}

        {/* candles */}
        {candles.map((c, i) => {
          const x = i * step + step / 2
          const up = c.close >= c.open
          const color = up ? "var(--color-bull)" : "var(--color-bear)"
          const bodyTop = yFor(Math.max(c.open, c.close))
          const bodyH = Math.max(1, Math.abs(yFor(c.open) - yFor(c.close)))
          return (
            <g key={i} opacity={hover != null && hover !== i ? 0.5 : 1}>
              <line x1={x} y1={yFor(c.high)} x2={x} y2={yFor(c.low)} stroke={color} strokeWidth={0.8} />
              <rect x={x - cw / 2} y={bodyTop} width={cw} height={bodyH} fill={color} rx={0.4} />
            </g>
          )
        })}

        {/* volume */}
        {showVolume &&
          candles.map((c, i) => {
            const x = i * step + step / 2
            const up = c.close >= c.open
            const vh = (c.volume / vMax) * (volH - 4)
            return (
              <rect
                key={i}
                x={x - cw / 2}
                y={height - vh}
                width={cw}
                height={vh}
                fill={up ? "var(--color-bull)" : "var(--color-bear)"}
                opacity={0.35}
              />
            )
          })}

        {/* crosshair */}
        {hover != null && (
          <line
            x1={hover * step + step / 2}
            y1={0}
            x2={hover * step + step / 2}
            y2={priceH}
            stroke="var(--color-text-muted)"
            strokeWidth={0.5}
            strokeDasharray="3 3"
          />
        )}
      </svg>

      {active && (
        <div className="pointer-events-none absolute left-2 top-2 flex gap-3 rounded-md border border-border-subtle bg-bg-base/90 px-2.5 py-1 font-mono text-[10px] backdrop-blur-sm">
          <span className="text-text-muted">O <span className="text-text-primary">{active.open.toFixed(2)}</span></span>
          <span className="text-text-muted">H <span className="text-signal-bull">{active.high.toFixed(2)}</span></span>
          <span className="text-text-muted">L <span className="text-signal-bear">{active.low.toFixed(2)}</span></span>
          <span className="text-text-muted">C <span className="text-text-primary">{active.close.toFixed(2)}</span></span>
        </div>
      )}
    </div>
  )
}
