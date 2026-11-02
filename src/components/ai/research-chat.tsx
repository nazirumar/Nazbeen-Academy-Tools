'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  BrainCircuit,
  CircleAlert,
  FlaskConical,
  Loader2,
  MessageSquarePlus,
  Send,
  Trash2,
  User,
} from 'lucide-react'
import { toast } from '@/hooks/use-toast'
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
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { api } from '@/lib/api/client'
import { ApiError } from '@/lib/api/types'
import type {
  AIChatResponse,
  AIConversationDetail,
  AIConversationSummary,
  AIStatus,
  AnalysisReportSummary,
  BacktestRunListItem,
  PaginatedAIConversations,
  PaginatedAnalysisReports,
  PaginatedBacktestRuns,
} from '@/lib/api/types'
import { cn } from '@/lib/utils'

function errorText(e: unknown): string {
  if (e instanceof ApiError) {
    const detail = e.details.length
      ? ` (${e.details.map((d) => `${d.path || ''} ${d.message ?? ''}`).join('; ')})`
      : ''
    return `${e.message}${detail}`
  }
  return e instanceof Error ? e.message : 'Unexpected error'
}

const SUGGESTIONS = [
  'Why did my selected strategy fail its conditions?',
  'Which conditions were inconclusive or unsupported?',
  'What evidence supports the reported market regime?',
  'Which data-quality issues affect this analysis?',
  'What are the limitations of this backtest?',
]

interface ContextSelection {
  analysisId: string | null
  backtestId: string | null
}

interface AnalysisOption {
  id: string
  label: string
}

interface BacktestOption {
  id: string
  label: string
}

function RoleBadge({ role }: { role: string }) {
  if (role === 'user') {
    return (
      <Badge variant="outline" className="gap-1 text-[10px]">
        <User className="h-3 w-3" aria-hidden /> You
      </Badge>
    )
  }
  if (role === 'error') {
    return (
      <Badge
        variant="outline"
        className="gap-1 border-red-600/50 text-[10px] text-red-700 dark:text-red-400"
      >
        <CircleAlert className="h-3 w-3" aria-hidden /> Error
      </Badge>
    )
  }
  return (
    <Badge
      variant="outline"
      className="gap-1 border-violet-500/50 text-[10px] text-violet-700 dark:text-violet-300"
    >
      <BrainCircuit className="h-3 w-3" aria-hidden /> AI · interpretation
    </Badge>
  )
}

/**
 * Phase 7 research chat: owner-scoped conversations about saved analyses
 * and backtests. Deterministic artefacts (selected report, its timestamp,
 * strategy version) are always visible; AI answers are labelled as
 * interpretation and must carry evidence references.
 */
export function ResearchChat({ mode }: { mode: string }) {
  const [status, setStatus] = useState<AIStatus | null>(null)
  const [conversations, setConversations] = useState<PaginatedAIConversations | null>(null)
  const [activeId, setActiveId] = useState<string | null>(null)
  const [detail, setDetail] = useState<AIConversationDetail | null>(null)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [sending, setSending] = useState(false)
  const [draft, setDraft] = useState('')
  const [analyses, setAnalyses] = useState<AnalysisOption[]>([])
  const [backtests, setBacktests] = useState<BacktestOption[]>([])
  const [selectedAnalysis, setSelectedAnalysis] = useState<string>('')
  const [selectedBacktest, setSelectedBacktest] = useState<string>('')
  const threadRef = useRef<HTMLDivElement>(null)

  const unconfigured = status !== null && (!status.enabled || !status.provider_configured)

  const loadConversations = useCallback(async () => {
    try {
      const page = await api.get<PaginatedAIConversations>(
        '/api/v1/ai/research/conversations',
        { page_size: '50' },
      )
      setConversations(page)
      return page
    } catch {
      setConversations(null)
      return null
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    api
      .get<AIStatus>('/api/v1/ai/status')
      .then((s) => {
        if (!cancelled) setStatus(s)
      })
      .catch(() => {
        if (!cancelled) setStatus(null)
      })
    void loadConversations()

    // Context options: recent analyses + backtest runs (owner-scoped).
    api
      .get<PaginatedAnalysisReports>('/api/v1/analysis/reports', { page_size: '20' })
      .then((page) => {
        if (cancelled) return
        setAnalyses(
          (page.items ?? []).map((r: AnalysisReportSummary) => ({
            id: r.id,
            label: `${r.symbol} · ${r.strategy_name} (v${r.strategy_version}) · ${r.evaluated_at.slice(0, 16).replace('T', ' ')}`,
          })),
        )
      })
      .catch(() => setAnalyses([]))
    api
      .get<PaginatedBacktestRuns>('/api/v1/backtests', { page_size: '20' })
      .then((page) => {
        if (cancelled) return
        setBacktests(
          (page.items ?? []).map((r: BacktestRunListItem) => ({
            id: r.id,
            label: `${r.symbol} · ${r.timeframe} · ${r.strategy_name} (v${r.strategy_version}) · ${r.status}`,
          })),
        )
      })
      .catch(() => setBacktests([]))
    return () => {
      cancelled = true
    }
  }, [loadConversations])

  const openConversation = useCallback(async (id: string) => {
    setActiveId(id)
    setLoadingDetail(true)
    setDetail(null)
    try {
      const d = await api.get<AIConversationDetail>(
        `/api/v1/ai/research/conversations/${id}`,
      )
      setDetail(d)
      setSelectedAnalysis(d.conversation.analysis_id ?? '')
      setSelectedBacktest(d.conversation.backtest_id ?? '')
    } catch (e) {
      toast({ title: 'Could not load conversation', description: errorText(e), variant: 'destructive' })
    } finally {
      setLoadingDetail(false)
    }
  }, [])

  useEffect(() => {
    const el = threadRef.current
    if (el && typeof el.scrollTo === 'function') {
      el.scrollTo({ top: el.scrollHeight })
    }
  }, [detail?.messages.length, sending])

  async function send(question: string) {
    const trimmed = question.trim()
    if (!trimmed || sending) return
    const selection: ContextSelection = {
      analysisId: selectedAnalysis || null,
      backtestId: selectedBacktest || null,
    }
    setSending(true)
    try {
      const resp = await api.post<AIChatResponse>('/api/v1/ai/research/chat', {
        message: trimmed,
        conversation_id: activeId,
        analysis_id: selection.analysisId,
        backtest_id: selection.backtestId,
      })
      setActiveId(resp.conversation.id)
      setDetail({ conversation: resp.conversation, messages: resp.messages })
      setDraft('')
      void loadConversations()
    } catch (e) {
      toast({
        title: 'The research request failed',
        description: errorText(e),
        variant: 'destructive',
      })
    } finally {
      setSending(false)
    }
  }

  async function removeConversation(id: string) {
    try {
      await api.delete(`/api/v1/ai/research/conversations/${id}`)
      if (activeId === id) {
        setActiveId(null)
        setDetail(null)
      }
      void loadConversations()
    } catch (e) {
      toast({ title: 'Could not delete conversation', description: errorText(e), variant: 'destructive' })
    }
  }

  function startNew() {
    setActiveId(null)
    setDetail(null)
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[320px_1fr] [&>*]:min-w-0">
      {/* Conversations ---------------------------------------------------- */}
      <Card className="order-2 lg:order-1">
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center justify-between text-base">
            Conversations
            <Button size="sm" variant="outline" onClick={startNew}>
              <MessageSquarePlus className="h-4 w-4" aria-hidden /> New
            </Button>
          </CardTitle>
          <CardDescription>
            Your research chats — scoped to your account, never shared.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {!conversations && (
            <p className="text-sm text-muted-foreground">
              Conversations unavailable (backend offline?).
            </p>
          )}
          {conversations && conversations.items.length === 0 && (
            <p className="text-sm text-muted-foreground" data-testid="conversations-empty">
              No research conversations yet. Attach an analysis or backtest and ask a question.
            </p>
          )}
          {conversations && conversations.items.length > 0 && (
            <ul className="divide-y text-sm" data-testid="conversation-list">
              {conversations.items.map((c) => (
                <li key={c.id} className="flex items-center justify-between gap-2 py-2">
                  <button
                    type="button"
                    className={cn(
                      'min-w-0 flex-1 rounded px-1 py-0.5 text-left hover:bg-muted/40',
                      activeId === c.id && 'font-semibold',
                    )}
                    onClick={() => void openConversation(c.id)}
                  >
                    <span className="block truncate">{c.title}</span>
                    <span className="block text-xs text-muted-foreground">
                      {c.message_count} messages · {c.updated_at.slice(0, 16).replace('T', ' ')}
                    </span>
                  </button>
                  <Button
                    size="icon"
                    variant="ghost"
                    className="h-7 w-7 text-muted-foreground"
                    aria-label={`Delete conversation ${c.title}`}
                    onClick={() => void removeConversation(c.id)}
                  >
                    <Trash2 className="h-3.5 w-3.5" aria-hidden />
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      {/* Thread ----------------------------------------------------------- */}
      <div className="order-1 space-y-4 lg:order-2">
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">Research context</CardTitle>
            <CardDescription>
              The assistant explains ONLY saved, owner-scoped results. Deterministic
              artefacts stay visible; AI answers are labelled interpretation.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground" htmlFor="ctx-analysis">
                Analysis report (optional)
              </label>
              <Select
                value={selectedAnalysis}
                onValueChange={(v) => setSelectedAnalysis(v === 'none-none' ? '' : v)}
              >
                <SelectTrigger id="ctx-analysis" aria-label="Attach analysis report">
                  <SelectValue placeholder={analyses.length ? 'Attach a stored analysis' : 'No stored analyses yet'} />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none-none">None</SelectItem>
                  {analyses.map((a) => (
                    <SelectItem key={a.id} value={a.id}>
                      {a.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground" htmlFor="ctx-backtest">
                Backtest run (optional)
              </label>
              <Select
                value={selectedBacktest}
                onValueChange={(v) => setSelectedBacktest(v === 'none-none' ? '' : v)}
              >
                <SelectTrigger id="ctx-backtest" aria-label="Attach backtest run">
                  <SelectValue placeholder={backtests.length ? 'Attach a backtest run' : 'No backtest runs yet'} />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none-none">None</SelectItem>
                  {backtests.map((b) => (
                    <SelectItem key={b.id} value={b.id}>
                      {b.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {(selectedAnalysis || selectedBacktest) && (
              <p className="text-xs text-muted-foreground sm:col-span-2" data-testid="context-chips">
                Context: {selectedAnalysis ? `analysis ${selectedAnalysis.slice(0, 8)}…` : ''}
                {selectedAnalysis && selectedBacktest ? ' + ' : ''}
                {selectedBacktest ? `backtest ${selectedBacktest.slice(0, 8)}…` : ''}
              </p>
            )}
          </CardContent>
        </Card>

        <Card className="flex min-h-[420px] flex-col">
          <CardHeader className="pb-2">
            <CardTitle className="text-base">
              {detail ? detail.conversation.title : 'New research conversation'}
            </CardTitle>
            {detail && (
              <CardDescription data-testid="conversation-context">
                Analysis: {detail.conversation.analysis_id?.slice(0, 8) ?? '—'} ·
                Backtest: {detail.conversation.backtest_id?.slice(0, 8) ?? '—'} ·{' '}
                {detail.conversation.message_count} messages
              </CardDescription>
            )}
          </CardHeader>
          <CardContent className="flex flex-1 flex-col gap-3">
            <div ref={threadRef} className="max-h-[420px] flex-1 space-y-3 overflow-y-auto pr-1" data-testid="chat-thread">
              {loadingDetail && (
                <p className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> Loading conversation…
                </p>
              )}
              {!loadingDetail && !detail && (
                <div className="flex h-full min-h-48 flex-col items-center justify-center gap-2 text-center text-sm text-muted-foreground" data-testid="chat-empty">
                  <BrainCircuit className="h-6 w-6" aria-hidden />
                  {unconfigured ? (
                    <p>
                      AI research is unavailable — the provider is not configured.
                      The deterministic analysis engine remains fully usable.
                    </p>
                  ) : (
                    <>
                      <p>Attach a saved analysis or backtest, then ask a question.</p>
                      <ul className="space-y-1 text-left text-xs">
                        {SUGGESTIONS.map((s) => (
                          <li key={s}>
                            <button
                              type="button"
                              className="underline decoration-dotted hover:text-foreground"
                              onClick={() => setDraft(s)}
                            >
                              {s}
                            </button>
                          </li>
                        ))}
                      </ul>
                    </>
                  )}
                </div>
              )}
              {detail?.messages.map((m) => (
                <div
                  key={m.id}
                  className={cn(
                    'rounded-lg border px-3 py-2',
                    m.role === 'user' && 'bg-muted/40',
                    m.role === 'error' && 'border-red-600/40 bg-red-500/5',
                    m.role === 'assistant' && 'border-violet-500/30',
                  )}
                  data-testid={`chat-message-${m.role}`}
                >
                  <div className="mb-1 flex flex-wrap items-center gap-2">
                    <RoleBadge role={m.role} />
                    {m.model && (
                      <Badge variant="outline" className="font-mono text-[10px]">
                        {m.provider} · {m.model}
                      </Badge>
                    )}
                    {m.validation && (
                      <Badge variant="outline" className="text-[10px]">
                        evidence refs checked: {m.validation.checked_refs}
                      </Badge>
                    )}
                  </div>
                  <p className="whitespace-pre-wrap text-sm">{m.content}</p>
                </div>
              ))}
              {sending && (
                <p className="flex items-center gap-2 text-sm text-muted-foreground" role="status" data-testid="chat-sending">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                  Researching the attached evidence…
                </p>
              )}
            </div>

            <div className="space-y-2">
              <Textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault()
                    void send(draft)
                  }
                }}
                placeholder={
                  unconfigured
                    ? 'AI research is unavailable: provider not configured.'
                    : 'Ask about the attached analysis or backtest… (Enter to send)'
                }
                disabled={unconfigured || sending}
                aria-label="Research question"
                className="min-h-20"
              />
              <div className="flex items-center justify-between gap-2">
                <p className="text-xs text-muted-foreground">
                  The assistant cannot change strategies, cannot execute code and
                  cannot place orders. It only explains saved evidence.
                </p>
                <Button
                  onClick={() => void send(draft)}
                  disabled={unconfigured || sending || !draft.trim()}
                  size="sm"
                >
                  <Send className="h-4 w-4" aria-hidden /> Send
                </Button>
              </div>
              {backtests.length > 0 && !detail && (
                <p className="flex items-center gap-1 text-xs text-muted-foreground">
                  <FlaskConical className="h-3 w-3" aria-hidden />
                  Tip: compare two backtests by attaching a run, then asking about
                  its recorded metrics — the assistant can only see stored data.
                </p>
              )}
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
