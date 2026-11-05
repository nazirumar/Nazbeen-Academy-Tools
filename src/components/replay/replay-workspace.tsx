'use client'

/**
 * Saved replay sessions workspace (Phase 11).
 *
 * History is owner-scoped and persisted server-side. Selecting a session
 * resumes at its saved cursor instead of starting over.
 */

import { useCallback, useEffect, useState } from 'react'
import { ChevronRight, History, Loader2, PlayCircle, Plus } from 'lucide-react'
import { toast } from '@/hooks/use-toast'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { api } from '@/lib/api/client'
import type {
  PaginatedReplaySessions,
  PlatformMode,
  ReplaySession,
  ReplaySessionListItem,
} from '@/lib/api/types'
import { cn } from '@/lib/utils'
import { ReplayConfigPanel } from './replay-config-panel'
import { ReplaySessionDetail } from './replay-session-detail'
import { REPLAY_STATUS_META, errorText, fmtReplayTime } from './replay-shared'

interface Props {
  mode: PlatformMode
}

export function ReplayWorkspace({ mode }: Props) {
  const [sessions, setSessions] = useState<ReplaySessionListItem[]>([])
  const [loading, setLoading] = useState(true)
  const [activeId, setActiveId] = useState<string | null>(null)
  const [showConfig, setShowConfig] = useState(true)

  const loadSessions = useCallback(async () => {
    setLoading(true)
    try {
      const data = await api.get<PaginatedReplaySessions>(
        '/api/v1/replay/sessions',
        { mode, page_size: '25' },
      )
      setSessions(data.items)
    } catch (e) {
      toast({
        title: 'Could not load replay history',
        description: errorText(e),
        variant: 'destructive',
      })
    } finally {
      setLoading(false)
    }
  }, [mode])

  useEffect(() => {
    setActiveId(null)
    setShowConfig(true)
    void loadSessions()
  }, [loadSessions])

  const onCreated = (session: ReplaySession) => {
    setActiveId(session.id)
    setShowConfig(false)
    void loadSessions()
  }

  const onBack = () => {
    setActiveId(null)
    void loadSessions()
  }

  const onDeleted = () => {
    setActiveId(null)
    setShowConfig(false)
    void loadSessions()
  }

  return (
    <div className="space-y-4">
      <Alert>
        <AlertDescription>
          Replay walks stored PostgreSQL candles candle-by-candle. A saved
          session keeps its cursor across reloads; playback speed is
          presentation-only and never changes chronology. This first slice does
          not execute trades, promise profitability, or invent missing candles.
        </AlertDescription>
      </Alert>

      <Card>
        <CardHeader className="pb-2">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div>
              <CardTitle className="flex items-center gap-2 text-base">
                <History className="h-4 w-4" aria-hidden /> Saved replays
              </CardTitle>
              <CardDescription>
                Historical sessions for this mode. Select one to resume from its
                persisted cursor.
              </CardDescription>
            </div>
            <Button
              size="sm"
              variant={showConfig ? 'secondary' : 'default'}
              onClick={() => {
                setShowConfig((value) => !value)
                if (showConfig) setActiveId(null)
              }}
            >
              <Plus className="h-4 w-4" aria-hidden /> New replay
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div
              className="flex items-center justify-center py-8 text-muted-foreground"
              data-testid="replay-history-loading"
            >
              <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden /> Loading saved replays…
            </div>
          ) : sessions.length === 0 ? (
            <p
              className="py-8 text-center text-sm text-muted-foreground"
              data-testid="replay-history-empty"
            >
              No replay sessions yet for this mode. Configure your first session below.
            </p>
          ) : (
            <div className="overflow-x-auto" data-testid="replay-history-table">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Symbol / TF</TableHead>
                    <TableHead>Window (UTC)</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead className="text-right">Visible</TableHead>
                    <TableHead className="text-right">Progress</TableHead>
                    <TableHead>Data quality</TableHead>
                    <TableHead>Created</TableHead>
                    <TableHead aria-label="Open replay" />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {sessions.map((item) => {
                    const status = REPLAY_STATUS_META[item.status]
                    return (
                      <TableRow
                        key={item.id}
                        className={cn('cursor-pointer', activeId === item.id && 'bg-muted/50')}
                        onClick={() => {
                          setActiveId(item.id)
                          setShowConfig(false)
                        }}
                      >
                        <TableCell className="text-xs">
                          <span className="font-medium">{item.symbol}</span> · {item.timeframe}
                        </TableCell>
                        <TableCell className="whitespace-nowrap text-xs">
                          {fmtReplayTime(item.start_time)} → {fmtReplayTime(item.end_time)}
                        </TableCell>
                        <TableCell>
                          <Badge className={status.className}>{status.label}</Badge>
                        </TableCell>
                        <TableCell className="text-right tabular-nums text-xs">
                          {item.visible_candles.toLocaleString()} / {item.total_candles.toLocaleString()}
                        </TableCell>
                        <TableCell className="text-right tabular-nums text-xs">
                          {(item.progress * 100).toFixed(1)}%
                        </TableCell>
                        <TableCell className="text-xs">
                          {item.data_quality_status === 'ok' ? (
                            <Badge variant="outline">complete</Badge>
                          ) : (
                            <Badge variant="outline" className="border-amber-500 text-amber-700 dark:text-amber-300">
                              partial
                            </Badge>
                          )}
                        </TableCell>
                        <TableCell className="whitespace-nowrap text-xs">
                          {fmtReplayTime(item.created_at)}
                        </TableCell>
                        <TableCell>
                          <ChevronRight className="h-4 w-4 text-muted-foreground" aria-hidden />
                        </TableCell>
                      </TableRow>
                    )
                  })}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      {showConfig ? (
        <ReplayConfigPanel mode={mode} onCreated={onCreated} />
      ) : activeId ? null : (
        <Alert>
          <AlertDescription className="flex items-center gap-2">
            <PlayCircle className="h-4 w-4" aria-hidden /> Create a new replay or pick a saved
            session above.
          </AlertDescription>
        </Alert>
      )}

      {activeId ? (
        <ReplaySessionDetail
          key={activeId}
          sessionId={activeId}
          onBack={onBack}
          onDeleted={onDeleted}
        />
      ) : null}
    </div>
  )
}
