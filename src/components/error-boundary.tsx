'use client'

/**
 * Class error boundary for dashboard subtrees. A rendering crash in the
 * chart or report must never blank the whole application; the fallback
 * offers a reset and keeps the rest of the app usable.
 */

import { Component, type ErrorInfo, type ReactNode } from 'react'
import { AlertTriangle } from 'lucide-react'
import { Button } from '@/components/ui/button'

interface Props {
  children: ReactNode
  label?: string
}

interface State {
  error: Error | null
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // No credentials or market data in logs — component + message only.
    console.error('[error-boundary]', this.props.label ?? 'dashboard', error.message, info.componentStack)
  }

  render(): ReactNode {
    if (this.state.error) {
      return (
        <div
          role="alert"
          className="rounded-lg border border-destructive/40 bg-destructive/5 p-4"
        >
          <p className="flex items-center gap-2 text-sm font-medium text-destructive">
            <AlertTriangle className="h-4 w-4" aria-hidden />
            {this.props.label ? `${this.props.label} crashed` : 'This section crashed'}
          </p>
          <p className="mt-1 text-xs text-muted-foreground">{this.state.error.message}</p>
          <Button
            size="sm"
            variant="outline"
            className="mt-2"
            onClick={() => this.setState({ error: null })}
          >
            Try again
          </Button>
        </div>
      )
    }
    return this.props.children
  }
}
