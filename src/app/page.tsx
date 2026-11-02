'use client'

import { useEffect } from 'react'
import { ChartLine, ChartCandlestick, FlaskConical, Gauge, LayoutDashboard, Library, BrainCircuit } from 'lucide-react'
import { AppShell } from '@/components/layout/app-shell'
import { SystemStatus } from '@/components/status/system-status'
import { MT5StatusPanel } from '@/components/market/mt5-status-panel'
import { MarketPreview } from '@/components/market/market-preview'
import { StrategyLibrary } from '@/components/strategies/strategy-library'
import { AnalysisWorkbench } from '@/components/analysis/analysis-workbench'
import { MarketDashboard } from '@/components/dashboard/market-dashboard'
import { BacktestWorkspace } from '@/components/backtest/backtest-workspace'
import { ResearchChat } from '@/components/ai/research-chat'
import { ErrorBoundary } from '@/components/error-boundary'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useModeStore } from '@/lib/platform/mode-store'

export default function Home() {
  const mode = useModeStore((state) => state.mode)
  const setMode = useModeStore((state) => state.setMode)

  useEffect(() => {
    // Manual rehydration: first paint matches SSR, then the persisted
    // preference (if any) is applied without hydration mismatches.
    void useModeStore.persist.rehydrate()
  }, [])

  return (
    <AppShell mode={mode} onModeChange={setMode}>
      <Tabs defaultValue="dashboard" className="gap-6">
        <TabsList className="w-full justify-start overflow-x-auto sm:w-auto">
          <TabsTrigger value="dashboard" className="gap-1.5">
            <Gauge className="h-4 w-4" aria-hidden /> Dashboard
          </TabsTrigger>
          <TabsTrigger value="overview" className="gap-1.5">
            <LayoutDashboard className="h-4 w-4" aria-hidden /> System
          </TabsTrigger>
          <TabsTrigger value="market" className="gap-1.5">
            <ChartCandlestick className="h-4 w-4" aria-hidden /> Market
          </TabsTrigger>
          <TabsTrigger value="strategies" className="gap-1.5">
            <Library className="h-4 w-4" aria-hidden /> Strategies
          </TabsTrigger>
          <TabsTrigger value="analysis" className="gap-1.5">
            <ChartLine className="h-4 w-4" aria-hidden /> Analysis
          </TabsTrigger>
          <TabsTrigger value="backtests" className="gap-1.5">
            <FlaskConical className="h-4 w-4" aria-hidden /> Backtests
          </TabsTrigger>
          <TabsTrigger value="research" className="gap-1.5">
            <BrainCircuit className="h-4 w-4" aria-hidden /> AI Research
          </TabsTrigger>
        </TabsList>

        <TabsContent value="dashboard" className="mt-0">
          <ErrorBoundary label="Market dashboard">
            <MarketDashboard key={mode} mode={mode} />
          </ErrorBoundary>
        </TabsContent>

        <TabsContent value="overview" className="mt-0 space-y-4">
          <SystemStatus />
          <MT5StatusPanel />
        </TabsContent>

        <TabsContent value="market" className="mt-0">
          <MarketPreview />
        </TabsContent>

        <TabsContent value="strategies" className="mt-0">
          <StrategyLibrary key={mode} mode={mode} />
        </TabsContent>

        <TabsContent value="analysis" className="mt-0">
          <AnalysisWorkbench key={mode} mode={mode} />
        </TabsContent>

        <TabsContent value="backtests" className="mt-0">
          <ErrorBoundary label="Backtesting workspace">
            <BacktestWorkspace key={mode} mode={mode} />
          </ErrorBoundary>
        </TabsContent>

        <TabsContent value="research" className="mt-0">
          <ErrorBoundary label="AI research chat">
            <ResearchChat key={mode} mode={mode} />
          </ErrorBoundary>
        </TabsContent>
      </Tabs>
    </AppShell>
  )
}
