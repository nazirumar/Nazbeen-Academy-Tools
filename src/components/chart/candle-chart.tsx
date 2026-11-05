'use client'

/**
 * Interactive candlestick chart (TradingView Lightweight Charts v5, Apache-2.0).
 *
 * Architecture rules:
 * - the chart object is created ONCE per mount and never recreated on
 *   refresh; polled data is applied with `series.update()` when only the
 *   tail changed and `setData()` on series identity changes / history
 *   corrections;
 * - data comes exclusively from the backend (`CandlePoint[]`); the
 *   component knows nothing about fetching;
 * - overlays are ONLY what `OverlayCatalog` contains (already derived from
 *   the analysis report by `overlay-model.ts`);
 * - full cleanup: chart.remove() tears down canvas, observers and the
 *   subscription used for the crosshair readout;
 * - timestamps are unix SECONDS rendered by the library in UTC
 *   (`timeScale.tickMarkFormatter` + crosshair label override); candle
 *   times follow the MT5 server-time convention documented by the API —
 *   the chart never silently re-bases them.
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  LineStyle,
  createChart,
  createSeriesMarkers,
} from 'lightweight-charts'
import type {
  IChartApi,
  ISeriesApi,
  ISeriesMarkersPluginApi,
  SeriesMarker,
  Time,
  UTCTimestamp,
} from 'lightweight-charts'
import type { CandlePoint } from '@/lib/api/types'
import type { OverlayCatalog } from '@/lib/chart/overlay-model'
import { BandPrimitive, type BandRect } from '@/lib/chart/band-primitive'

export interface CandleChartProps {
  candles: CandlePoint[]
  /** Series identity — changing it performs a full `setData` + fit. */
  seriesKey: string
  /** Decimal digits for the price scale (from the symbol metadata). */
  digits?: number
  overlays: OverlayCatalog
  visible: Record<string, boolean>
  /** Whether the newest candle is forming (drives the honest label). */
  includesForming: boolean
  /** Keep the viewport pinned to the newest candle (market replay). */
  followLatest?: boolean
  heightClass?: string
}

const UP = '#26a69a'
const DOWN = '#ef5350'

interface ThemeColors {
  text: string
  grid: string
  border: string
  crosshair: string
}

function readThemeColors(): ThemeColors {
  if (typeof window === 'undefined' || typeof document === 'undefined') {
    return { text: '#94a3b8', grid: 'rgba(148,163,184,0.12)', border: 'rgba(148,163,184,0.3)', crosshair: '#64748b' }
  }
  const style = getComputedStyle(document.documentElement)
  // Tailwind v4 theme values can compute to modern color spaces (oklch/lab)
  // which Lightweight Charts cannot parse. Normalising through a canvas
  // fillStyle round-trip converts any valid CSS colour to #rrggbb / rgba().
  const raw = (name: string, fallback: string) => style.getPropertyValue(name).trim() || fallback
  const text = normalizeColor(raw('--muted-foreground', '#94a3b8'), '#94a3b8')
  const border = normalizeColor(raw('--border', 'rgba(148,163,184,0.3)'), 'rgba(148,163,184,0.3)')
  const crosshair = normalizeColor(raw('--foreground', '#64748b'), '#64748b')
  return {
    text,
    grid: withAlpha(border, 0.16),
    border,
    crosshair,
  }
}

/**
 * Convert ANY valid CSS colour string into one of the formats Lightweight
 * Charts parses (#rrggbb, rgba(r,g,b,a)). Modern colour spaces (Tailwind
 * v4's oklch/lab) are resolved by painting them into a 1x1 canvas and
 * reading the pixel back — the browser's colour engine does the conversion.
 * Returns the fallback for invalid colours or when nothing was painted.
 */
function normalizeColor(value: string, fallback: string): string {
  if (typeof document === 'undefined') return fallback
  try {
    const canvas = document.createElement('canvas')
    canvas.width = 1
    canvas.height = 1
    const ctx = canvas.getContext('2d', { willReadFrequently: true })
    if (!ctx) return fallback
    ctx.clearRect(0, 0, 1, 1)
    ctx.fillStyle = value
    ctx.fillRect(0, 0, 1, 1)
    const [r, g, b, a] = ctx.getImageData(0, 0, 1, 1).data
    if (a === 0) return fallback // nothing painted (invalid colour)
    if (a === 255) {
      const hex = (n: number) => n.toString(16).padStart(2, '0')
      return `#${hex(r)}${hex(g)}${hex(b)}`
    }
    return `rgba(${r}, ${g}, ${b}, ${+(a / 255).toFixed(3)})`
  } catch {
    return fallback
  }
}

/** Apply an alpha channel to a normalized colour string. */
function withAlpha(color: string, alpha: number): string {
  if (color.startsWith('#')) {
    const hex = color.slice(1)
    if (hex.length === 6) return `${color}${Math.round(alpha * 255).toString(16).padStart(2, '0')}`
    if (hex.length === 8) return `#${hex.slice(0, 6)}${Math.round(alpha * 255).toString(16).padStart(2, '0')}`
    return color
  }
  const rgba = color.match(/rgba?\(([^)]+)\)/)
  if (rgba) {
    const parts = rgba[1].split(/[\s,/]+/).filter(Boolean)
    const [r, g, b] = parts
    if (r !== undefined && g !== undefined && b !== undefined) {
      return `rgba(${r}, ${g}, ${b}, ${alpha})`
    }
  }
  return color
}

const TONE_COLORS = {
  bullish: { fill: 'rgba(38,166,154,0.16)', border: 'rgba(38,166,154,0.55)' },
  bearish: { fill: 'rgba(239,83,80,0.16)', border: 'rgba(239,83,80,0.55)' },
  neutral: { fill: 'rgba(148,163,184,0.14)', border: 'rgba(100,116,139,0.55)' },
} as const

function formatAxisTime(seconds: number): string {
  // Candle times are MT5 server-time epoch seconds; rendered as UTC to
  // match every other timestamp in the platform (documented behaviour).
  const d = new Date(seconds * 1000)
  const hh = String(d.getUTCHours()).padStart(2, '0')
  const mm = String(d.getUTCMinutes()).padStart(2, '0')
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(
    d.getUTCDate(),
  ).padStart(2, '0')} ${hh}:${mm}`
}

function toChartMarkers(
  overlays: OverlayCatalog,
  visible: Record<string, boolean>,
): SeriesMarker<Time>[] {
  const out: SeriesMarker<Time>[] = []
  for (const m of overlays.markers) {
    if (!visible[m.kind]) continue
    if (m.kind === 'structure_label' && !m.text) continue
    out.push({
      time: m.time as UTCTimestamp,
      position: m.anchor === 'above' ? 'aboveBar' : 'belowBar',
      shape: m.kind === 'swing_low' ? 'arrowUp' : m.kind === 'swing_high' ? 'arrowDown' : 'circle',
      color:
        m.kind === 'structure_label'
          ? m.tone === 'bullish'
            ? UP
            : m.tone === 'bearish'
              ? DOWN
              : '#64748b'
          : '#64748b',
      text: m.text,
      size: m.kind === 'structure_label' ? 1 : 1,
    })
  }
  // Markers must be time-ascending; equal times keep stable order.
  return out.sort((a, b) => (a.time as number) - (b.time as number))
}

function toBandRects(
  overlays: OverlayCatalog,
  visible: Record<string, boolean>,
): BandRect[] {
  const rects: BandRect[] = []
  for (const level of overlays.levels) {
    if (!visible[level.kind]) continue
    const tone = TONE_COLORS[level.tone]
    rects.push({
      id: level.id,
      time1: level.time1,
      time2: level.time2,
      price1: level.price,
      price2: level.price,
      color: tone.fill,
      borderColor: tone.border,
      lineThicknessPx: 2,
    })
  }
  for (const band of overlays.bands) {
    if (!visible[band.kind]) continue
    const tone = TONE_COLORS[band.tone]
    rects.push({
      id: band.id,
      time1: band.time1,
      time2: band.time2,
      price1: band.price1,
      price2: band.price2,
      color: tone.fill,
      borderColor: tone.border,
    })
  }
  return rects
}

interface HoverReadout {
  time: number
  open: number
  high: number
  low: number
  close: number
  forming: boolean
}

export function CandleChart({
  candles,
  seriesKey,
  digits = 5,
  overlays,
  visible,
  includesForming,
  followLatest = false,
  heightClass = 'h-[420px]',
}: CandleChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null)
  const primitiveRef = useRef<BandPrimitive | null>(null)
  const appliedRef = useRef<{ key: string; candles: CandlePoint[] }>({ key: '', candles: [] })
  const [hover, setHover] = useState<HoverReadout | null>(null)

  const candlesRef = useRef(candles)
  // Ref mutation inside an effect (never during render) — the crosshair
  // handler reads it on mouse events, i.e. after effects have run.
  useEffect(() => {
    candlesRef.current = candles
  }, [candles])

  // ---------------------------------------------------------------- mount
  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    let theme = readThemeColors()
    const chart = createChart(container, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: theme.text,
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: theme.grid, style: LineStyle.Solid },
        horzLines: { color: theme.grid, style: LineStyle.Solid },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: theme.crosshair, labelBackgroundColor: theme.crosshair },
        horzLine: { color: theme.crosshair, labelBackgroundColor: theme.crosshair },
      },
      rightPriceScale: {
        borderVisible: true,
        borderColor: theme.border,
        scaleMargins: { top: 0.12, bottom: 0.12 },
      },
      timeScale: {
        borderVisible: true,
        borderColor: theme.border,
        rightOffset: 4,
        barSpacing: 7,
        tickMarkFormatter: (t: Time) => formatAxisTime(t as number),
      },
      localization: {
        priceFormatter: (p: number) => p.toFixed(digits),
      },
    })
    const series = chart.addSeries(CandlestickSeries, {
      upColor: UP,
      downColor: DOWN,
      borderUpColor: UP,
      borderDownColor: DOWN,
      wickUpColor: UP,
      wickDownColor: DOWN,
    })

    const primitive = new BandPrimitive([])
    series.attachPrimitive(primitive)
    const markers = createSeriesMarkers(series, [])

    const crosshairHandler = (param: { time?: Time; seriesData: Map<unknown, unknown> }) => {
      if (param.time === undefined) {
        setHover(null)
        return
      }
      const bar = param.seriesData.get(series) as
        | { open: number; high: number; low: number; close: number }
        | undefined
      if (!bar) {
        setHover(null)
        return
      }
      const time = param.time as number
      const point = candlesRef.current.find((c) => c.time === time)
      setHover({
        time,
        open: bar.open,
        high: bar.high,
        low: bar.low,
        close: bar.close,
        forming: point?.forming ?? false,
      })
    }
    chart.subscribeCrosshairMove(crosshairHandler)

    // Re-read CSS theme colours when the <html> class changes (dark mode).
    const observer = new MutationObserver(() => {
      const next = readThemeColors()
      if (next.text === theme.text && next.grid === theme.grid && next.border === theme.border) {
        return
      }
      theme = next
      chart.applyOptions({
        layout: { textColor: theme.text },
        grid: {
          vertLines: { color: theme.grid, style: LineStyle.Solid },
          horzLines: { color: theme.grid, style: LineStyle.Solid },
        },
        crosshair: {
          vertLine: { color: theme.crosshair, labelBackgroundColor: theme.crosshair },
          horzLine: { color: theme.crosshair, labelBackgroundColor: theme.crosshair },
        },
        rightPriceScale: { borderColor: theme.border },
        timeScale: { borderColor: theme.border },
      })
    })
    if (typeof document !== 'undefined') {
      observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
    }

    chartRef.current = chart
    seriesRef.current = series
    markersRef.current = markers
    primitiveRef.current = primitive

    return () => {
      observer.disconnect()
      chart.unsubscribeCrosshairMove(crosshairHandler)
      chart.remove()
      chartRef.current = null
      seriesRef.current = null
      markersRef.current = null
      primitiveRef.current = null
      appliedRef.current = { key: '', candles: [] }
    }
  }, [])

  // ------------------------------------------------------------- data in
  useEffect(() => {
    const chart = chartRef.current
    const series = seriesRef.current
    if (!chart || !series) return
    const applied = appliedRef.current

    // Defensive, contract-enforcing conversion: ascending by time, finite
    // values only (lightweight-charts rejects unsorted/invalid data).
    const toChartData = (list: CandlePoint[]) =>
      [...list]
        .filter((c) => Number.isFinite(c.time) && Number.isFinite(c.close))
        .sort((a, b) => a.time - b.time)
        .map((c) => ({
          time: c.time as UTCTimestamp,
          open: c.open,
          high: c.high,
          low: c.low,
          close: c.close,
        }))

    // Series identity changed (symbol/timeframe switch): replace everything.
    if (applied.key !== seriesKey) {
      series.setData(toChartData(candles))
      chart.timeScale().fitContent()
      appliedRef.current = { key: seriesKey, candles }
      return
    }

    if (applied.candles === candles) return // merged array was unchanged

    // Find whether only the tail differs (polling case) or history changed.
    const oldLen = applied.candles.length
    const shared = Math.min(oldLen, candles.length)
    let firstDiff = shared
    for (let i = 0; i < shared; i++) {
      const a = applied.candles[i]
      const b = candles[i]
      if (
        a.time !== b.time ||
        a.open !== b.open ||
        a.high !== b.high ||
        a.low !== b.low ||
        a.close !== b.close
      ) {
        firstDiff = i
        break
      }
    }

    if (candles.length < oldLen || firstDiff < oldLen - 1) {
      // History changed before the tail, or the window shrank: full resync
      // (no fitContent — the user's zoom/pan is preserved).
      series.setData(toChartData(candles))
      if (followLatest) chart.timeScale().fitContent()
    } else {
      // Tail-only change: the last old candle changed (closed over forming)
      // and/or new candles arrived — push each tail candle incrementally.
      for (let i = Math.max(0, firstDiff); i < candles.length; i++) {
        const c = candles[i]
        series.update({
          time: c.time as UTCTimestamp,
          open: c.open,
          high: c.high,
          low: c.low,
          close: c.close,
        })
      }
      if (followLatest) chart.timeScale().scrollToRealTime()
    }
    appliedRef.current = { key: seriesKey, candles }
  }, [candles, seriesKey, followLatest])

  // ---------------------------------------------------------- overlays
  const chartMarkers = useMemo(() => toChartMarkers(overlays, visible), [overlays, visible])
  const chartRects = useMemo(() => toBandRects(overlays, visible), [overlays, visible])

  useEffect(() => {
    markersRef.current?.setMarkers(chartMarkers)
  }, [chartMarkers])

  useEffect(() => {
    primitiveRef.current?.setRects(chartRects)
  }, [chartRects])

  // digits changes (new symbol selected): update price formatting live.
  useEffect(() => {
    chartRef.current?.applyOptions({
      localization: { priceFormatter: (p: number) => p.toFixed(digits) },
    })
  }, [digits])

  const lastPoint = candles.length > 0 ? candles[candles.length - 1] : null

  return (
    <div className="relative" data-testid="candle-chart">
      <div ref={containerRef} className={`w-full ${heightClass}`} role="img"
        aria-label={`Candlestick chart with ${candles.length} candles`} />
      {/* Legend / crosshair readout — real values only, forming labelled. */}
      <div className="pointer-events-none absolute left-2 top-2 rounded-md border bg-background/85 px-2.5 py-1.5 text-xs shadow-sm backdrop-blur">
        <span className="font-mono font-semibold">{seriesKey}</span>
        {hover ? (
          <span className="ml-2 font-mono text-muted-foreground">
            O {hover.open.toFixed(digits)} H {hover.high.toFixed(digits)} L{' '}
            {hover.low.toFixed(digits)} C {hover.close.toFixed(digits)}
          </span>
        ) : (
          lastPoint && (
            <span className="ml-2 font-mono text-muted-foreground">
              C {lastPoint.close.toFixed(digits)}
            </span>
          )
        )}
        {(hover?.forming || (!hover && includesForming && lastPoint?.forming)) && (
          <span className="ml-2 rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-medium text-amber-600 dark:text-amber-400">
            forming — not final
          </span>
        )}
      </div>
    </div>
  )
}
