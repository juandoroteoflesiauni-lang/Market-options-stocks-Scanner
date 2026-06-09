"use client"

import { useId } from "react"

interface MiniSparklineProps {
  data: number[]
  width?: number
  height?: number
  positive?: boolean
  strokeWidth?: number
  fill?: boolean
}

export function MiniSparkline({
  data,
  width = 96,
  height = 28,
  positive,
  strokeWidth = 1.5,
  fill = true,
}: MiniSparklineProps) {
  const gradId = useId()
  if (!data || data.length < 2) {
    return <div style={{ width, height }} aria-hidden />
  }

  const min = Math.min(...data)
  const max = Math.max(...data)
  const range = max - min || 1
  const stepX = width / (data.length - 1)

  const points = data.map((d, i) => {
    const x = i * stepX
    const y = height - ((d - min) / range) * (height - strokeWidth * 2) - strokeWidth
    return [x, y] as const
  })

  const up = positive ?? data[data.length - 1] >= data[0]
  const stroke = up ? "var(--color-bull)" : "var(--color-bear)"

  const line = points.map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`).join(" ")
  const area = `${line} L${width},${height} L0,${height} Z`

  return (
    <svg width={width} height={height} className="overflow-visible" role="img" aria-label="trend sparkline">
      {fill && (
        <defs>
          <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={stroke} stopOpacity="0.28" />
            <stop offset="100%" stopColor={stroke} stopOpacity="0" />
          </linearGradient>
        </defs>
      )}
      {fill && <path d={area} fill={`url(#${gradId})`} stroke="none" />}
      <path d={line} fill="none" stroke={stroke} strokeWidth={strokeWidth} strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  )
}
