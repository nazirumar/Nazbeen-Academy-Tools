/**
 * Rectangular band primitive for Lightweight Charts v5.
 *
 * Draws price/time rectangles (FVG zones, strategy price zones) and
 * zero-height price segments (BOS / MSS levels) that span a time range.
 * Coordinates are computed inside `draw` from the live chart state so
 * pan/zoom is always correct without stale caches.
 *
 * The primitive draws ONLY the rectangles it is given — it knows nothing
 * about market data, strategies or analysis semantics.
 */

import type {
  IChartApi,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  PrimitivePaneViewZOrder,
  SeriesAttachedParameter,
  SeriesType,
  Time,
} from "lightweight-charts";

export interface BandRect {
  id: string;
  /** Unix seconds (candle open times present in the series data). */
  time1: number;
  time2: number;
  price1: number;
  price2: number;
  /** Fill colour, any CSS colour string (alpha recommended). */
  color: string;
  borderColor: string;
  /** Height in px when price1 === price2 (horizontal level segment). */
  lineThicknessPx?: number;
}

interface PixelRect {
  x1: number;
  x2: number;
  y1: number;
  y2: number;
  rect: BandRect;
}

/** Structural type of the bitmap render target (avoids fancy-canvas import). */
interface RenderTarget {
  useBitmapCoordinateSpace: (
    cb: (scope: {
      context: CanvasRenderingContext2D;
      horizontalPixelRatio: number;
      verticalPixelRatio: number;
    }) => void,
  ) => void;
}

class BandPaneRenderer implements IPrimitivePaneRenderer {
  constructor(private readonly _rects: PixelRect[]) {}

  draw(target: RenderTarget): void {
    target.useBitmapCoordinateSpace((scope) => {
      const ctx = scope.context;
      const hr = scope.horizontalPixelRatio;
      const vr = scope.verticalPixelRatio;
      for (const r of this._rects) {
        const isLine = r.rect.price1 === r.rect.price2;
        if (isLine) {
          const thickness = (r.rect.lineThicknessPx ?? 1.5) * vr;
          const y = r.y1;
          ctx.fillStyle = r.rect.borderColor;
          ctx.fillRect(
            Math.min(r.x1, r.x2),
            y - thickness / 2,
            Math.abs(r.x2 - r.x1),
            thickness,
          );
        } else {
          const top = Math.min(r.y1, r.y2);
          const height = Math.abs(r.y2 - r.y1);
          if (height < 1) continue;
          ctx.fillStyle = r.rect.color;
          ctx.fillRect(Math.min(r.x1, r.x2), top, Math.abs(r.x2 - r.x1), height);
          ctx.strokeStyle = r.rect.borderColor;
          ctx.lineWidth = 1;
          ctx.strokeRect(
            Math.min(r.x1, r.x2),
            top,
            Math.abs(r.x2 - r.x1),
            height,
          );
        }
      }
    });
  }
}

class BandPaneView implements IPrimitivePaneView {
  private readonly _renderer: BandPaneRenderer;

  constructor(rects: PixelRect[]) {
    this._renderer = new BandPaneRenderer(rects);
  }

  zOrder(): PrimitivePaneViewZOrder {
    return "bottom";
  }

  renderer(): IPrimitivePaneRenderer {
    return this._renderer;
  }
}

export class BandPrimitive implements ISeriesPrimitive<Time> {
  private _chart: IChartApi | null = null;
  private _series: ISeriesApi<SeriesType> | null = null;
  private _rects: BandRect[] = [];
  private _requestUpdate: (() => void) | null = null;

  constructor(rects: BandRect[] = []) {
    this._rects = rects;
  }

  setRects(rects: BandRect[]): void {
    this._rects = rects;
    this._requestUpdate?.();
  }

  attached(param: SeriesAttachedParameter<Time>): void {
    this._chart = param.chart;
    this._series = param.series as unknown as ISeriesApi<SeriesType>;
    this._requestUpdate = param.requestUpdate;
    // Redraw on pan/zoom so pixel coordinates stay correct.
    param.chart.timeScale().subscribeVisibleLogicalRangeChange(this._handleRangeChange);
  }

  detached(): void {
    this._chart?.timeScale().unsubscribeVisibleLogicalRangeChange(this._handleRangeChange);
    this._chart = null;
    this._series = null;
    this._requestUpdate = null;
  }

  private _handleRangeChange = (): void => {
    this._requestUpdate?.();
  };

  private _computePixelRects(): PixelRect[] {
    const chart = this._chart;
    const series = this._series;
    if (!chart || !series) return [];
    const timeScale = chart.timeScale();
    const out: PixelRect[] = [];
    for (const rect of this._rects) {
      const x1 = timeScale.timeToCoordinate(rect.time1 as Time);
      const x2 = timeScale.timeToCoordinate(rect.time2 as Time);
      const y1 = series.priceToCoordinate(rect.price1);
      const y2 = series.priceToCoordinate(rect.price2);
      // Times are always candle opens present in the data; if the chart data
      // no longer contains them we skip the rect instead of guessing.
      if (x1 === null || x2 === null || y1 === null || y2 === null) continue;
      out.push({ x1, x2, y1, y2, rect });
    }
    return out;
  }

  paneViews(): IPrimitivePaneView[] {
    return [new BandPaneView(this._computePixelRects())];
  }
}
