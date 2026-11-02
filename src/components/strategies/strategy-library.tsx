'use client'

/**
 * Strategy Library (Phase 3) — management view for categories,
 * subcategories, strategies and their versions.
 *
 * The heavy editing happens in the Strategy Builder (full view); this
 * component lists, organises, duplicates, archives and deletes. Destructive
 * operations always confirm first and are refused by the backend while
 * dependents exist — nothing is silently removed.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Archive,
  ArchiveRestore,
  ChevronRight,
  Copy,
  FilePlus2,
  FolderPlus,
  History,
  Loader2,
  Pencil,
  PenLine,
  Rocket,
  Trash2,
} from 'lucide-react'
import { toast } from '@/hooks/use-toast'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Textarea } from '@/components/ui/textarea'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import { api } from '@/lib/api/client'
import { StrategyBuilder } from '@/components/strategies/strategy-builder'
import {
  ApiError,
  type Category,
  type PaginatedStrategies,
  type PlatformMode,
  type Strategy,
  type StrategyDirection,
  type StrategyVersion,
} from '@/lib/api/types'

type LoadState =
  | { kind: 'loading' }
  | { kind: 'ready' }
  | { kind: 'error'; message: string; requestId?: string | null }

function errorText(e: unknown): string {
  if (e instanceof ApiError) {
    const detail = e.details.length
      ? ` (${e.details.map((d) => `${d.path || ''} ${d.message ?? ''}`).join('; ')})`
      : ''
    return `${e.message}${detail}${e.requestId ? ` [${e.requestId}]` : ''}`
  }
  return e instanceof Error ? e.message : 'Unexpected error'
}

interface StrategyLibraryProps {
  mode: PlatformMode
}

export function StrategyLibrary({ mode }: StrategyLibraryProps) {
  const [view, setView] = useState<'library' | 'builder'>('library')
  const [builderFor, setBuilderFor] = useState<string | null>(null)

  const [state, setState] = useState<LoadState>({ kind: 'loading' })
  const [categories, setCategories] = useState<Category[]>([])
  const [page, setPage] = useState<PaginatedStrategies | null>(null)
  const [pageNum, setPageNum] = useState(1)
  const [showArchived, setShowArchived] = useState(false)

  // create-strategy dialog
  const [createOpen, setCreateOpen] = useState(false)
  const [form, setForm] = useState({
    name: '',
    direction: 'bullish' as StrategyDirection,
    category: 'none',
    subcategory: 'none',
    description: '',
  })
  const [creating, setCreating] = useState(false)

  // category dialogs
  const [catDialog, setCatDialog] = useState<
    | { kind: 'create-root' }
    | { kind: 'create-sub'; parentId: string }
    | { kind: 'rename'; category: Category }
    | null
  >(null)
  const [catName, setCatName] = useState('')
  const [catBusy, setCatBusy] = useState(false)

  // confirm dialogs
  const [confirmAction, setConfirmAction] = useState<
    | { kind: 'delete-strategy'; strategy: Strategy }
    | { kind: 'delete-category'; category: Category }
    | { kind: 'archive-category'; category: Category }
    | { kind: 'archive-strategy'; strategy: Strategy }
    | null
  >(null)

  const loadCategories = useCallback(async () => {
    const cats = await api.get<Category[]>('/api/v1/strategy-categories', {
      mode,
      include_archived: 'true',
    })
    setCategories(cats)
  }, [mode])

  const loadStrategies = useCallback(
    async (targetPage = pageNum) => {
      const data = await api.get<PaginatedStrategies>('/api/v1/strategies', {
        mode,
        page: String(targetPage),
        page_size: '10',
        ...(showArchived ? { include_archived: 'true' } : {}),
      })
      setPage(data)
    },
    [mode, pageNum, showArchived],
  )

  const loadData = useCallback(async () => {
    setState({ kind: 'loading' })
    try {
      await Promise.all([loadCategories(), loadStrategies()])
      setState({ kind: 'ready' })
    } catch (e) {
      setState({ kind: 'error', message: errorText(e) })
    }
  }, [loadCategories, loadStrategies])

  useEffect(() => {
    void loadData()
  }, [loadData])

  const categoryById = useMemo(() => {
    const map = new Map<string, Category>()
    const walk = (cats: Category[]) => {
      for (const c of cats) {
        map.set(c.id, c)
        if (c.children) walk(c.children)
      }
    }
    walk(categories)
    return map
  }, [categories])

  const roots = categories.filter((c) => !c.parent_id)

  async function createStrategy() {
    if (!form.name.trim()) return
    setCreating(true)
    try {
      const categorySelection =
        form.subcategory !== 'none'
          ? form.subcategory
          : form.category !== 'none'
            ? form.category
            : null
      const created = await api.post<Strategy>('/api/v1/strategies', {
        name: form.name.trim(),
        mode,
        direction: form.direction,
        description: form.description.trim() || null,
        category_id: categorySelection,
      })
      toast({ title: 'Strategy created', description: form.name.trim() })
      setCreateOpen(false)
      setForm({ name: '', direction: 'bullish', category: 'none', subcategory: 'none', description: '' })
      await loadData()
      // Open the builder for the freshly created strategy.
      setBuilderFor(created.id)
      setView('builder')
    } catch (e) {
      toast({ title: 'Could not create strategy', description: errorText(e), variant: 'destructive' })
    } finally {
      setCreating(false)
    }
  }

  async function submitCategoryDialog() {
    if (!catDialog) return
    setCatBusy(true)
    try {
      if (catDialog.kind === 'create-root') {
        await api.post('/api/v1/strategy-categories', { name: catName.trim(), mode })
        toast({ title: 'Category created', description: catName.trim() })
      } else if (catDialog.kind === 'create-sub') {
        await api.post('/api/v1/strategy-categories', {
          name: catName.trim(),
          mode,
          parent_id: catDialog.parentId,
        })
        toast({ title: 'Subcategory created', description: catName.trim() })
      } else if (catDialog.kind === 'rename') {
        await api.patch(`/api/v1/strategy-categories/${catDialog.category.id}`, {
          name: catName.trim(),
        })
        toast({ title: 'Category renamed', description: catName.trim() })
      }
      setCatDialog(null)
      setCatName('')
      await loadCategories()
    } catch (e) {
      toast({ title: 'Could not save category', description: errorText(e), variant: 'destructive' })
    } finally {
      setCatBusy(false)
    }
  }

  async function runConfirmed() {
    if (!confirmAction) return
    try {
      if (confirmAction.kind === 'delete-strategy') {
        await api.delete(`/api/v1/strategies/${confirmAction.strategy.id}`)
        toast({ title: 'Strategy deleted', description: confirmAction.strategy.name })
      } else if (confirmAction.kind === 'delete-category') {
        await api.delete(`/api/v1/strategy-categories/${confirmAction.category.id}`)
        toast({ title: 'Category deleted', description: confirmAction.category.name })
      } else if (confirmAction.kind === 'archive-category') {
        await api.post(`/api/v1/strategy-categories/${confirmAction.category.id}/archive`)
        toast({ title: 'Category archived', description: confirmAction.category.name })
      } else if (confirmAction.kind === 'archive-strategy') {
        await api.post(`/api/v1/strategies/${confirmAction.strategy.id}/archive`)
        toast({ title: 'Strategy archived', description: confirmAction.strategy.name })
      }
      setConfirmAction(null)
      await loadData()
    } catch (e) {
      toast({ title: 'Operation failed', description: errorText(e), variant: 'destructive' })
    }
  }

  async function unarchiveCategory(category: Category) {
    try {
      await api.post(`/api/v1/strategy-categories/${category.id}/unarchive`)
      toast({ title: 'Category restored', description: category.name })
      await loadData()
    } catch (e) {
      toast({ title: 'Could not restore', description: errorText(e), variant: 'destructive' })
    }
  }

  async function unarchiveStrategy(strategy: Strategy) {
    try {
      await api.post(`/api/v1/strategies/${strategy.id}/unarchive`)
      toast({ title: 'Strategy restored', description: strategy.name })
      await loadData()
    } catch (e) {
      toast({ title: 'Could not restore', description: errorText(e), variant: 'destructive' })
    }
  }

  async function duplicateStrategy(strategy: Strategy) {
    try {
      const res = await api.post<{ strategy: Strategy }>(
        `/api/v1/strategies/${strategy.id}/duplicate`,
        {},
      )
      toast({ title: 'Duplicated', description: res.strategy.name })
      await loadData()
    } catch (e) {
      toast({ title: 'Could not duplicate', description: errorText(e), variant: 'destructive' })
    }
  }

  async function publishLatest(strategy: Strategy) {
    try {
      await api.post(
        `/api/v1/strategies/${strategy.id}/versions/${strategy.current_version}/publish`,
      )
      toast({
        title: `Published v${strategy.current_version}`,
        description: 'The definition is now immutable.',
      })
      await loadData()
    } catch (e) {
      toast({ title: 'Publish failed', description: errorText(e), variant: 'destructive' })
    }
  }

  async function showVersions(strategy: Strategy) {
    try {
      const vers = await api.get<StrategyVersion[]>(`/api/v1/strategies/${strategy.id}/versions`)
      const lines = vers
        .map(
          (v) =>
            `v${v.version} · ${v.status} · ${v.changelog ?? 'no changelog'}`,
        )
        .join('\n')
      toast({ title: `Versions of ${strategy.name}`, description: lines || 'No versions yet' })
    } catch (e) {
      toast({ title: 'Could not load versions', description: errorText(e), variant: 'destructive' })
    }
  }

  // ------------------------------------------------------------------ builder
  if (view === 'builder' && builderFor) {
    return (
      <StrategyBuilder
        strategyId={builderFor}
        mode={mode}
        categories={categories}
        onCategoriesChanged={loadCategories}
        onClose={() => {
          setView('library')
          setBuilderFor(null)
          void loadData()
        }}
        onSaved={loadData}
      />
    )
  }

  const directionBadge = (d: StrategyDirection) => (
    <Badge
      variant="outline"
      className={
        d === 'bullish'
          ? 'border-emerald-600/40 text-emerald-700 dark:text-emerald-400'
          : d === 'bearish'
            ? 'border-red-600/40 text-red-700 dark:text-red-400'
            : 'border-violet-600/40 text-violet-700 dark:text-violet-400'
      }
    >
      {d === 'bullish' ? '▲ Bullish' : d === 'bearish' ? '▼ Bearish' : '◆ Both'}
    </Badge>
  )

  return (
    <div className="grid gap-4 [&>*]:min-w-0 lg:grid-cols-[280px_1fr]">
      {/* Categories ------------------------------------------------------- */}
      <Card className="self-start">
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Categories</CardTitle>
          <CardDescription>Your own organisation of setups</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <Button
            size="sm"
            variant="outline"
            className="w-full"
            onClick={() => {
              setCatName('')
              setCatDialog({ kind: 'create-root' })
            }}
          >
            <FolderPlus className="h-4 w-4" aria-hidden /> New category
          </Button>
          <ScrollArea className="max-h-96 pr-2">
            <ul className="space-y-1">
              {roots.length === 0 && (
                <li className="py-6 text-center text-sm text-muted-foreground">
                  No categories yet for this mode.
                </li>
              )}
              {roots.map((c) => (
                <li key={c.id} className="group">
                  <div
                    className={`flex items-center justify-between rounded-md px-2 py-1.5 text-sm hover:bg-muted/60 ${c.is_archived ? 'opacity-60' : ''}`}
                  >
                    <span className="flex min-w-0 items-center gap-1.5">
                      <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
                      <span className="truncate">{c.name}</span>
                      {c.is_archived && (
                        <Badge variant="outline" className="shrink-0 text-[10px]">
                          archived
                        </Badge>
                      )}
                    </span>
                    <span className="ml-2 flex shrink-0 items-center gap-0.5">
                      <Badge variant="secondary">{c.strategy_count ?? 0}</Badge>
                      <TooltipProvider delayDuration={150}>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <Button
                              size="icon"
                              variant="ghost"
                              className="h-6 w-6"
                              aria-label={`Add subcategory to ${c.name}`}
                              onClick={() => {
                                setCatName('')
                                setCatDialog({ kind: 'create-sub', parentId: c.id })
                              }}
                            >
                              <FolderPlus className="h-3 w-3" aria-hidden />
                            </Button>
                          </TooltipTrigger>
                          <TooltipContent>Add subcategory</TooltipContent>
                        </Tooltip>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <Button
                              size="icon"
                              variant="ghost"
                              className="h-6 w-6"
                              aria-label={`Rename ${c.name}`}
                              onClick={() => {
                                setCatName(c.name)
                                setCatDialog({ kind: 'rename', category: c })
                              }}
                            >
                              <PenLine className="h-3 w-3" aria-hidden />
                            </Button>
                          </TooltipTrigger>
                          <TooltipContent>Rename</TooltipContent>
                        </Tooltip>
                        {c.is_archived ? (
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <Button
                                size="icon"
                                variant="ghost"
                                className="h-6 w-6"
                                aria-label={`Restore ${c.name}`}
                                onClick={() => void unarchiveCategory(c)}
                              >
                                <ArchiveRestore className="h-3 w-3" aria-hidden />
                              </Button>
                            </TooltipTrigger>
                            <TooltipContent>Restore</TooltipContent>
                          </Tooltip>
                        ) : (
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <Button
                                size="icon"
                                variant="ghost"
                                className="h-6 w-6"
                                aria-label={`Archive ${c.name}`}
                                onClick={() => setConfirmAction({ kind: 'archive-category', category: c })}
                              >
                                <Archive className="h-3 w-3" aria-hidden />
                              </Button>
                            </TooltipTrigger>
                            <TooltipContent>Archive (non-destructive)</TooltipContent>
                          </Tooltip>
                        )}
                      </TooltipProvider>
                    </span>
                  </div>
                  {c.children && c.children.length > 0 && (
                    <ul className="ml-6 space-y-1 border-l pl-2">
                      {c.children.map((sub) => (
                        <li
                          key={sub.id}
                          className={`flex items-center justify-between rounded-md px-2 py-1 text-xs hover:bg-muted/60 ${sub.is_archived ? 'opacity-60' : ''}`}
                        >
                          <span className="flex min-w-0 items-center gap-1.5">
                            <span className="truncate">{sub.name}</span>
                            <Badge variant="secondary" className="shrink-0">
                              {sub.strategy_count ?? 0}
                            </Badge>
                          </span>
                          <span className="flex shrink-0 items-center">
                            {sub.is_archived ? (
                              <Button
                                size="icon"
                                variant="ghost"
                                className="h-6 w-6"
                                aria-label={`Restore ${sub.name}`}
                                onClick={() => void unarchiveCategory(sub)}
                              >
                                <ArchiveRestore className="h-3 w-3" aria-hidden />
                              </Button>
                            ) : (
                              <Button
                                size="icon"
                                variant="ghost"
                                className="h-6 w-6"
                                aria-label={`Archive ${sub.name}`}
                                onClick={() => setConfirmAction({ kind: 'archive-category', category: sub })}
                              >
                                <Archive className="h-3 w-3" aria-hidden />
                              </Button>
                            )}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </li>
              ))}
            </ul>
          </ScrollArea>
        </CardContent>
      </Card>

      {/* Strategies ------------------------------------------------------- */}
      <Card>
        <CardHeader className="pb-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <CardTitle className="text-base">Strategies</CardTitle>
              <CardDescription>
                {mode === 'forex' ? 'Forex' : 'Binary Options'} mode · draft →
                publish workflow, immutable versions
              </CardDescription>
            </div>
            <div className="flex items-center gap-2">
              <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <input
                  type="checkbox"
                  checked={showArchived}
                  onChange={(e) => {
                    setShowArchived(e.target.checked)
                    setPageNum(1)
                  }}
                  aria-label="Show archived strategies"
                />
                Show archived
              </label>
              <Button size="sm" onClick={() => setCreateOpen(true)}>
                <FilePlus2 className="h-4 w-4" aria-hidden /> New strategy
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {state.kind === 'loading' && (
            <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> Loading strategies…
            </div>
          )}
          {state.kind === 'error' && (
            <p className="py-6 text-sm text-destructive">{state.message}</p>
          )}
          {state.kind === 'ready' && page && page.items.length === 0 && (
            <p className="py-6 text-center text-sm text-muted-foreground">
              No strategies yet. Create one to open the Strategy Builder.
            </p>
          )}
          {state.kind === 'ready' && page && page.items.length > 0 && (
            <>
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Name</TableHead>
                      <TableHead>Category</TableHead>
                      <TableHead>Direction</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead className="text-right">Versions</TableHead>
                      <TableHead className="w-28 text-right">Actions</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {page.items.map((s) => (
                      <TableRow key={s.id} className={s.status === 'archived' ? 'opacity-60' : ''}>
                        <TableCell className="font-medium">
                          <button
                            type="button"
                            className="text-left hover:underline"
                            onClick={() => {
                              setBuilderFor(s.id)
                              setView('builder')
                            }}
                            aria-label={`Open builder for ${s.name}`}
                          >
                            {s.name}
                          </button>
                        </TableCell>
                        <TableCell className="text-muted-foreground">
                          {s.category_id ? categoryById.get(s.category_id)?.name ?? '—' : '—'}
                        </TableCell>
                        <TableCell>{directionBadge(s.direction)}</TableCell>
                        <TableCell>
                          <div className="flex flex-wrap items-center gap-1">
                            <Badge variant="outline" className="capitalize">
                              {s.status}
                            </Badge>
                            {s.published_version_number !== null && (
                              <Badge
                                variant="outline"
                                className="border-emerald-600/40 text-emerald-700 dark:text-emerald-400"
                              >
                                pub v{s.published_version_number}
                              </Badge>
                            )}
                          </div>
                        </TableCell>
                        <TableCell className="text-right font-mono text-xs">
                          {s.current_version > 0 ? `v${s.current_version}` : 'no rules'}
                        </TableCell>
                        <TableCell className="text-right">
                          <TooltipProvider delayDuration={150}>
                            <div className="flex justify-end gap-0.5">
                              <Tooltip>
                                <TooltipTrigger asChild>
                                  <Button
                                    size="icon"
                                    variant="ghost"
                                    className="h-7 w-7"
                                    aria-label={`Edit ${s.name}`}
                                    onClick={() => {
                                      setBuilderFor(s.id)
                                      setView('builder')
                                    }}
                                  >
                                    <Pencil className="h-3.5 w-3.5" aria-hidden />
                                  </Button>
                                </TooltipTrigger>
                                <TooltipContent>Open in Strategy Builder</TooltipContent>
                              </Tooltip>
                              <Tooltip>
                                <TooltipTrigger asChild>
                                  <Button
                                    size="icon"
                                    variant="ghost"
                                    className="h-7 w-7"
                                    aria-label={`Versions of ${s.name}`}
                                    onClick={() => void showVersions(s)}
                                  >
                                    <History className="h-3.5 w-3.5" aria-hidden />
                                  </Button>
                                </TooltipTrigger>
                                <TooltipContent>Version history</TooltipContent>
                              </Tooltip>
                              <Tooltip>
                                <TooltipTrigger asChild>
                                  <Button
                                    size="icon"
                                    variant="ghost"
                                    className="h-7 w-7"
                                    aria-label={`Publish ${s.name}`}
                                    disabled={s.current_version === 0 || s.status === 'archived'}
                                    onClick={() => void publishLatest(s)}
                                  >
                                    <Rocket className="h-3.5 w-3.5" aria-hidden />
                                  </Button>
                                </TooltipTrigger>
                                <TooltipContent>Publish latest version</TooltipContent>
                              </Tooltip>
                              <Tooltip>
                                <TooltipTrigger asChild>
                                  <Button
                                    size="icon"
                                    variant="ghost"
                                    className="h-7 w-7"
                                    aria-label={`Duplicate ${s.name}`}
                                    onClick={() => void duplicateStrategy(s)}
                                  >
                                    <Copy className="h-3.5 w-3.5" aria-hidden />
                                  </Button>
                                </TooltipTrigger>
                                <TooltipContent>Duplicate</TooltipContent>
                              </Tooltip>
                              {s.status === 'archived' ? (
                                <Tooltip>
                                  <TooltipTrigger asChild>
                                    <Button
                                      size="icon"
                                      variant="ghost"
                                      className="h-7 w-7"
                                      aria-label={`Restore ${s.name}`}
                                      onClick={() => void unarchiveStrategy(s)}
                                    >
                                      <ArchiveRestore className="h-3.5 w-3.5" aria-hidden />
                                    </Button>
                                  </TooltipTrigger>
                                  <TooltipContent>Restore</TooltipContent>
                                </Tooltip>
                              ) : (
                                <Tooltip>
                                  <TooltipTrigger asChild>
                                    <Button
                                      size="icon"
                                      variant="ghost"
                                      className="h-7 w-7"
                                      aria-label={`Archive ${s.name}`}
                                      onClick={() => setConfirmAction({ kind: 'archive-strategy', strategy: s })}
                                    >
                                      <Archive className="h-3.5 w-3.5" aria-hidden />
                                    </Button>
                                  </TooltipTrigger>
                                  <TooltipContent>Archive</TooltipContent>
                                </Tooltip>
                              )}
                              <Tooltip>
                                <TooltipTrigger asChild>
                                  <Button
                                    size="icon"
                                    variant="ghost"
                                    className="h-7 w-7"
                                    aria-label={`Delete ${s.name}`}
                                    onClick={() => setConfirmAction({ kind: 'delete-strategy', strategy: s })}
                                  >
                                    <Trash2 className="h-3.5 w-3.5 text-destructive" aria-hidden />
                                  </Button>
                                </TooltipTrigger>
                                <TooltipContent>Delete (removes all versions)</TooltipContent>
                              </Tooltip>
                            </div>
                          </TooltipProvider>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
              {page.page.pages > 1 && (
                <div className="mt-3 flex items-center justify-between text-xs text-muted-foreground">
                  <span>
                    Page {page.page.page} of {page.page.pages} · {page.page.total} strategies
                  </span>
                  <span className="flex gap-1">
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={page.page.page <= 1}
                      onClick={() => setPageNum((p) => Math.max(1, p - 1))}
                    >
                      Previous
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={page.page.page >= page.page.pages}
                      onClick={() => setPageNum((p) => p + 1)}
                    >
                      Next
                    </Button>
                  </span>
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>

      {/* Create strategy dialog ------------------------------------------- */}
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>New strategy ({mode === 'forex' ? 'Forex' : 'Binary Options'})</DialogTitle>
            <DialogDescription>
              The Strategy Builder opens right after creation. Binary Options
              strategies default to the M1 timeframe.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="strategy-name">Name</Label>
              <Input
                id="strategy-name"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="e.g. M1 pullback continuation"
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label>Direction</Label>
                <Select
                  value={form.direction}
                  onValueChange={(v) => setForm({ ...form, direction: v as StrategyDirection })}
                >
                  <SelectTrigger aria-label="Direction">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="bullish">Bullish</SelectItem>
                    <SelectItem value="bearish">Bearish</SelectItem>
                    <SelectItem value="both">Both</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label>Category</Label>
                <Select
                  value={form.category}
                  onValueChange={(v) => setForm({ ...form, category: v, subcategory: 'none' })}
                >
                  <SelectTrigger aria-label="Category">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">None</SelectItem>
                    {roots
                      .filter((c) => !c.is_archived)
                      .map((c) => (
                        <SelectItem key={c.id} value={c.id}>
                          {c.name}
                        </SelectItem>
                      ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
            {form.category !== 'none' && (
              <div className="space-y-1.5">
                <Label>Subcategory (optional)</Label>
                <Select
                  value={form.subcategory}
                  onValueChange={(v) => setForm({ ...form, subcategory: v })}
                >
                  <SelectTrigger aria-label="Subcategory">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">None</SelectItem>
                    {(categoryById.get(form.category)?.children ?? [])
                      .filter((c) => !c.is_archived)
                      .map((c) => (
                        <SelectItem key={c.id} value={c.id}>
                          {c.name}
                        </SelectItem>
                      ))}
                  </SelectContent>
                </Select>
              </div>
            )}
            <div className="space-y-1.5">
              <Label htmlFor="strategy-desc">Description (optional)</Label>
              <Textarea
                id="strategy-desc"
                rows={2}
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
                placeholder="What is the idea behind this setup?"
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateOpen(false)}>
              Cancel
            </Button>
            <Button onClick={() => void createStrategy()} disabled={creating || !form.name.trim()}>
              {creating && <Loader2 className="h-4 w-4 animate-spin" aria-hidden />} Create
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Category create/rename dialog -------------------------------------- */}
      <Dialog
        open={catDialog !== null}
        onOpenChange={(open) => !open && setCatDialog(null)}
      >
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>
              {catDialog?.kind === 'rename'
                ? 'Rename category'
                : catDialog?.kind === 'create-sub'
                  ? 'New subcategory'
                  : 'New category'}
            </DialogTitle>
            <DialogDescription>
              {catDialog?.kind === 'create-sub'
                ? 'Subcategories live one level under a category, in the same mode.'
                : 'Categories organise your strategies per analysis mode.'}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-1.5">
            <Label htmlFor="category-name">Name</Label>
            <Input
              id="category-name"
              value={catName}
              onChange={(e) => setCatName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void submitCategoryDialog()
              }}
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCatDialog(null)}>
              Cancel
            </Button>
            <Button onClick={() => void submitCategoryDialog()} disabled={catBusy || !catName.trim()}>
              {catBusy && <Loader2 className="h-4 w-4 animate-spin" aria-hidden />} Save
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Confirm destructive / archive actions -------------------------------- */}
      <AlertDialog
        open={confirmAction !== null}
        onOpenChange={(open) => !open && setConfirmAction(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {confirmAction?.kind === 'delete-strategy' && 'Delete this strategy?'}
              {confirmAction?.kind === 'delete-category' && 'Delete this category?'}
              {confirmAction?.kind === 'archive-category' && 'Archive this category?'}
              {confirmAction?.kind === 'archive-strategy' && 'Archive this strategy?'}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {confirmAction?.kind === 'delete-strategy' && (
                <>
                  "{confirmAction.strategy.name}" and ALL of its versions will be
                  removed permanently. Consider archiving instead.
                </>
              )}
              {confirmAction?.kind === 'delete-category' && (
                <>
                  "{confirmAction.category.name}" can only be deleted when it has no
                  subcategories and no strategies.
                </>
              )}
              {confirmAction?.kind === 'archive-category' && (
                <>
                  "{confirmAction.category.name}" and its subcategories will be hidden
                  from the default lists. Nothing is deleted; restore anytime.
                </>
              )}
              {confirmAction?.kind === 'archive-strategy' && (
                <>
                  "{confirmAction.strategy.name}" will be hidden from the default
                  list and cannot be published while archived. Nothing is deleted.
                </>
              )}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => void runConfirmed()}
              className={
                confirmAction?.kind.startsWith('delete')
                  ? 'bg-destructive text-destructive-foreground hover:bg-destructive/90'
                  : ''
              }
            >
              {confirmAction?.kind.startsWith('delete') ? 'Delete' : 'Archive'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
