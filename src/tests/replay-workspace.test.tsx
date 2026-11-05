/**
 * Phase 11 - replay workspace tests.
 *
 * These tests exercise the client contract only: history rendering, creation
 * after a real coverage check, saved-session resume, single-candle stepping,
 * playback toggle, seeking and error states. The backend's no-look-ahead
 * guarantee is asserted in backend integration tests; here the mocked series
 * mirrors what the API is allowed to return.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ReplayWorkspace } from '@/components/replay/replay-workspace'
import { ReplaySessionDetail } from '@/components/replay/replay-session-detail'
import { ApiError } from '@/lib/api/types'
import type {
  CandlePoint,
  ReplayCandleSeries,
  ReplayCoverage,
  ReplayDataQuality,
  ReplaySession,
  ReplaySessionListItem,
} from '@/lib/api/types'

const getMock = vi.fn()
const postMock = vi.fn()
const patchMock = vi.fn()
const deleteMock = vi.fn()
const toastMock = vi.fn()

vi.mock('@/hooks/use-toast', () => ({
  toast: (...args: unknown[]) => toastMock(...args),
}))

vi.mock('@/lib/api/client', () => ({
  api: {
    get: (...args: unknown[]) => getMock(...args),
    post: (...args: unknown[]) => postMock(...args),
    patch: (...args: unknown[]) => patchMock(...args),
    delete: (...args: unknown[]) => deleteMock(...args),
  },
}))

vi.mock('@/components/chart/candle-chart', () => ({
  CandleChart: ({ candles }: { candles: CandlePoint[] }) => (
    <div data-testid="candle-chart-stub">
      candles:{candles.length} last:{candles.at(-1)?.time ?? 'none'}
    </div>
  ),
}))

const FIRST = '2026-01-01T00:00:00.000Z'
const SECOND = '2026-01-01T00:15:00.000Z'
const THIRD = '2026-01-01T00:30:00.000Z'
const FOURTH = '2026-01-01T00:45:00.000Z'
const LAST = '2026-01-01T05:00:00.000Z'

const quality: ReplayDataQuality = {
  timeframe: 'M15',
  candles_available: 21,
  candles_available_raw: 22,
  expected_candles: 21,
  first_time: Math.floor(Date.parse(FIRST) / 1000),
  last_time: Math.floor(Date.parse(LAST) / 1000),
  gap_count: 0,
  largest_gap_intervals: 0,
  duplicate_count: 0,
  out_of_order_count: 0,
  forming_count: 1,
  invalid_ohlc_count: 0,
  coverage_ratio: 1,
  warnings: [
    {
      code: 'FORMING_CANDLE_EXCLUDED',
      severity: 'info',
      message: '1 forming stored candle was excluded from the replay series.',
    },
  ],
  overall_status: 'partial',
  closed_candles_only: true,
  forming_candles_used: false,
  source: 'postgres_candle_cache',
  source_note: 'Stored candles only.',
  note: 'No gaps were filled.',
}

function candle(time: string, price = 1.1): CandlePoint {
  return {
    time: Math.floor(Date.parse(time) / 1000),
    time_utc: time,
    open: price,
    high: price + 0.001,
    low: price - 0.001,
    close: price + 0.0005,
    volume: 100,
    forming: false,
  }
}

const ALL_CANDLES = [candle(FIRST), candle(SECOND), candle(THIRD), candle(FOURTH)]

function session(overrides: Partial<ReplaySession> = {}): ReplaySession {
  return {
    id: 'replay-1',
    owner_id: 'local',
    mode: 'forex',
    symbol: 'EURUSD',
    timeframe: 'M15',
    status: 'paused',
    start_time: '2025-12-31T23:00:00.000Z',
    end_time: LAST,
    first_candle_time: FIRST,
    last_candle_time: LAST,
    cursor_time: THIRD,
    current_time: FOURTH,
    current_time_unix: Math.floor(Date.parse(FOURTH) / 1000),
    visible_candles: 3,
    total_candles: 21,
    remaining_candles: 18,
    cursor_index: 2,
    progress: 3 / 21,
    progress_pct: 14.3,
    at_end: false,
    playback_speed: 1,
    data_quality: quality,
    config: { data_source: 'postgres_candle_cache' },
    session_schema_version: '1.0',
    engine_version: '1.0.0',
    created_at: '2026-01-02T00:00:00.000Z',
    last_advanced_at: null,
    no_lookahead: true,
    note: 'Future candles are never returned.',
    ...overrides,
  }
}

function listItem(overrides: Partial<ReplaySessionListItem> = {}): ReplaySessionListItem {
  return {
    id: 'replay-1',
    mode: 'forex',
    symbol: 'EURUSD',
    timeframe: 'M15',
    status: 'paused',
    start_time: '2025-12-31T23:00:00.000Z',
    end_time: LAST,
    cursor_time: THIRD,
    visible_candles: 3,
    total_candles: 21,
    progress: 3 / 21,
    playback_speed: 1,
    data_quality_status: 'partial',
    created_at: '2026-01-02T00:00:00.000Z',
    ...overrides,
  }
}

function series(visible: number, current: ReplaySession): ReplayCandleSeries {
  const candles = ALL_CANDLES.slice(0, visible)
  return {
    session_id: current.id,
    symbol: current.symbol,
    timeframe: current.timeframe,
    status: current.status,
    timezone: 'UTC',
    candles,
    count: candles.length,
    cursor_time: current.cursor_time,
    current_time: current.current_time,
    current_time_unix: current.current_time_unix,
    visible_candles: visible,
    total_candles: current.total_candles,
    progress: visible / current.total_candles,
    data_quality: current.data_quality,
    no_lookahead: true,
    future_candles_excluded: true,
    note: 'Only visible candles.',
    requested_limit: 2000,
    returned_limit: 2000,
  }
}

const coverage: ReplayCoverage = {
  symbol: 'EURUSD',
  timeframe: 'M15',
  start: '2025-12-31T23:00:00.000Z',
  end: LAST,
  candles_in_range: 21,
  expected_candles: 21,
  coverage_ratio: 1,
  first_time: Math.floor(Date.parse(FIRST) / 1000),
  last_time: Math.floor(Date.parse(LAST) / 1000),
  data_quality: quality,
  checked_at: '2026-01-02T00:00:00.000Z',
  note: 'Read-only coverage.',
}

beforeEach(() => {
  vi.clearAllMocks()
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('Replay workspace', () => {
  it('renders owner-scoped saved sessions and honest quality labels', async () => {
    getMock.mockImplementation((path: string) => {
      if (path === '/api/v1/replay/sessions') {
        return Promise.resolve({
          items: [listItem()],
          page: { page: 1, page_size: 25, total: 1, pages: 1 },
        })
      }
      return Promise.reject(new Error(`unexpected GET ${path}`))
    })

    render(<ReplayWorkspace mode="forex" />)

    expect(await screen.findByText('EURUSD')).toBeInTheDocument()
    expect(screen.getByText('Paused')).toBeInTheDocument()
    expect(screen.getByText('partial')).toBeInTheDocument()
    expect(screen.getByText('14.3%')).toBeInTheDocument()
  })

  it('shows an empty history state without inventing sessions', async () => {
    getMock.mockResolvedValue({
      items: [],
      page: { page: 1, page_size: 25, total: 0, pages: 1 },
    })

    render(<ReplayWorkspace mode="forex" />)

    expect(await screen.findByTestId('replay-history-empty')).toBeInTheDocument()
    expect(screen.queryByTestId('replay-history-table')).not.toBeInTheDocument()
  })

  it('checks coverage then creates and opens a paused session', async () => {
    const user = userEvent.setup()
    getMock.mockImplementation((path: string) => {
      if (path === '/api/v1/replay/sessions') {
        return Promise.resolve({
          items: [],
          page: { page: 1, page_size: 25, total: 0, pages: 1 },
        })
      }
      if (path === '/api/v1/replay/data-coverage') return Promise.resolve(coverage)
      if (path === '/api/v1/replay/sessions/replay-1') return Promise.resolve(session())
      if (path === '/api/v1/replay/sessions/replay-1/candles') {
        return Promise.resolve(series(3, session()))
      }
      return Promise.reject(new Error(`unexpected GET ${path}`))
    })
    postMock.mockResolvedValue(session())

    render(<ReplayWorkspace mode="forex" />)
    await screen.findByTestId('replay-history-empty')

    await user.click(screen.getByRole('button', { name: /Check stored data/i }))
    expect(await screen.findByTestId('replay-coverage')).toBeInTheDocument()
    expect(screen.getByText('Closed candles')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Create replay/i }))

    expect(await screen.findByText(/Historical replay · EURUSD M15/i)).toBeInTheDocument()
    expect(postMock).toHaveBeenCalledWith(
      '/api/v1/replay/sessions',
      expect.objectContaining({
        mode: 'forex',
        symbol: 'EURUSD',
        timeframe: 'M15',
        playback_speed: 1,
      }),
    )
    expect(await screen.findByTestId('candle-chart-stub')).toHaveTextContent('candles:3')
  })

  it('surfaces a backend creation error without creating a session', async () => {
    const user = userEvent.setup()
    getMock.mockResolvedValue({
      items: [],
      page: { page: 1, page_size: 25, total: 0, pages: 1 },
    })
    postMock.mockRejectedValue(
      new ApiError({
        code: 'NO_HISTORICAL_DATA',
        message: 'No stored closed candles exist in the requested window.',
        status: 422,
      }),
    )

    render(<ReplayWorkspace mode="forex" />)
    await screen.findByTestId('replay-history-empty')
    await user.click(screen.getByRole('button', { name: /Create replay/i }))

    expect(
      await screen.findByText(/No stored closed candles exist/i),
    ).toBeInTheDocument()
    expect(screen.queryByText(/Historical replay ·/i)).not.toBeInTheDocument()
  })
})

describe('Replay session detail', () => {
  it('steps exactly one candle at a time and requests the scoped series again', async () => {
    let visible = 3
    getMock.mockImplementation((path: string) => {
      if (path === '/api/v1/replay/sessions/replay-1') return Promise.resolve(session())
      if (path === '/api/v1/replay/sessions/replay-1/candles') {
        const current = session({ visible_candles: visible })
        return Promise.resolve(series(visible, current))
      }
      return Promise.reject(new Error(`unexpected GET ${path}`))
    })
    postMock.mockImplementation((path: string) => {
      if (path === '/api/v1/replay/sessions/replay-1/step') {
        visible += 1
        return Promise.resolve(
          session({
            visible_candles: visible,
            cursor_time: FOURTH,
            current_time: '2026-01-01T01:00:00.000Z',
            current_time_unix: Math.floor(Date.parse('2026-01-01T01:00:00.000Z') / 1000),
            cursor_index: 3,
            remaining_candles: 17,
            progress: visible / 21,
            progress_pct: 19.0,
          }),
        )
      }
      return Promise.reject(new Error(`unexpected POST ${path}`))
    })

    render(
      <ReplaySessionDetail
        sessionId="replay-1"
        onBack={vi.fn()}
        onDeleted={vi.fn()}
      />,
    )

    expect(await screen.findByTestId('replay-visible-count')).toHaveTextContent('3 / 21')
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: /Step forward one candle/i }))

    await waitFor(() =>
      expect(screen.getByTestId('replay-visible-count')).toHaveTextContent('4 / 21'),
    )
    expect(postMock).toHaveBeenCalledWith(
      '/api/v1/replay/sessions/replay-1/step',
      { count: 1 },
    )
    expect(getMock).toHaveBeenCalledWith(
      '/api/v1/replay/sessions/replay-1/candles',
      { limit: '2000' },
    )
    expect(screen.getByTestId('candle-chart-stub')).toHaveTextContent('candles:4')
  })

  it('toggles playback through the persisted resume endpoint', async () => {
    getMock.mockImplementation((path: string) => {
      if (path === '/api/v1/replay/sessions/replay-1') return Promise.resolve(session())
      if (path === '/api/v1/replay/sessions/replay-1/candles') {
        return Promise.resolve(series(3, session()))
      }
      return Promise.reject(new Error(`unexpected GET ${path}`))
    })
    postMock.mockResolvedValue(session({ status: 'active' }))

    render(
      <ReplaySessionDetail
        sessionId="replay-1"
        onBack={vi.fn()}
        onDeleted={vi.fn()}
      />,
    )

    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /^Play$/i }))

    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith('/api/v1/replay/sessions/replay-1/resume'),
    )
    expect(await screen.findByRole('button', { name: /Pause/i })).toBeInTheDocument()
  })

  it('seeks by timestamp and never submits a future target itself', async () => {
    getMock.mockImplementation((path: string) => {
      if (path === '/api/v1/replay/sessions/replay-1') return Promise.resolve(session())
      if (path === '/api/v1/replay/sessions/replay-1/candles') {
        return Promise.resolve(series(3, session()))
      }
      return Promise.reject(new Error(`unexpected GET ${path}`))
    })
    postMock.mockResolvedValue(
      session({
        visible_candles: 2,
        cursor_time: SECOND,
        current_time: THIRD,
        current_time_unix: Math.floor(Date.parse(THIRD) / 1000),
        cursor_index: 1,
      }),
    )

    render(
      <ReplaySessionDetail
        sessionId="replay-1"
        onBack={vi.fn()}
        onDeleted={vi.fn()}
      />,
    )

    const slider = await screen.findByLabelText('Seek to time')
    fireEvent.change(slider, {
      target: { value: String(Math.floor(Date.parse(SECOND) / 1000)) },
    })
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: /^Seek$/i }))

    await waitFor(() => expect(postMock).toHaveBeenCalled())
    expect(postMock).toHaveBeenCalledWith(
      '/api/v1/replay/sessions/replay-1/seek',
      { target_time: SECOND },
    )
  })

  it('renders a clear error state for an unavailable session', async () => {
    getMock.mockRejectedValue(
      new ApiError({
        code: 'NOT_FOUND',
        message: 'Replay session was not found.',
        status: 404,
      }),
    )

    render(
      <ReplaySessionDetail
        sessionId="missing"
        onBack={vi.fn()}
        onDeleted={vi.fn()}
      />,
    )

    expect(await screen.findByText(/Replay session was not found/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Back to saved replays/i })).toBeInTheDocument()
  })
})
