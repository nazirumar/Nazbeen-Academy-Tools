/**
 * Phase 7 — AI research layer frontend tests.
 *
 * Covers: explanation panel (unconfigured / loading / error / validated
 * result states, visual distinction from deterministic evidence), research
 * chat (empty / sending / error / message rendering, conversation list,
 * context visibility), and the honest provider-not-configured messaging.
 * The backend API is always mocked — no external LLM is contacted.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AIExplanationPanel } from "@/components/ai/ai-explanation-panel";
import { ResearchChat } from "@/components/ai/research-chat";
import { ApiError } from "@/lib/api/types";
import type {
  AIChatResponse,
  AIConversationDetail,
  AIExplanationResponse,
  AIStatus,
  PaginatedAIConversations,
  PaginatedAnalysisReports,
  PaginatedBacktestRuns,
} from "@/lib/api/types";

const getMock = vi.fn();
const postMock = vi.fn();
const deleteMock = vi.fn();
const toastMock = vi.fn();

vi.mock("@/hooks/use-toast", () => ({
  toast: (...args: unknown[]) => toastMock(...args),
}));

vi.mock("@/lib/api/client", () => ({
  api: {
    get: (...args: unknown[]) => getMock(...args),
    post: (...args: unknown[]) => postMock(...args),
    patch: vi.fn(),
    delete: (...args: unknown[]) => deleteMock(...args),
  },
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const statusConfigured: AIStatus = {
  enabled: true,
  provider_configured: true,
  provider: "openai_compatible",
  model: "test-model",
  timeout_seconds: 45,
  max_retries: 2,
  rate_limit_per_minute: 10,
  max_input_chars: 24000,
  max_output_tokens: 1400,
  explanations_endpoint: "/api/v1/ai/explanations",
  chat_endpoint: "/api/v1/ai/research/chat",
  note: "test",
};

const statusUnconfigured: AIStatus = {
  ...statusConfigured,
  enabled: false,
  provider_configured: false,
  model: null,
};

const explanationResponse: AIExplanationResponse = {
  analysis_id: "aaaaaaaa-1111-1111-1111-111111111111",
  strategy: {
    id: "bbbbbbbb-1111-1111-1111-111111111111",
    name: "Trend pullback",
    version: 3,
    version_status: "published",
    direction: "bullish",
  },
  mode: "forex",
  symbol: "EURUSD",
  evaluated_at: "2026-01-01T00:00:00.000+00:00",
  explanation: {
    summary: "The engine recorded conditions_missing on EURUSD.",
    market_context: "The regime evidence records a range classification.",
    conditions_met: [
      { text: "Close above 1.10 was met.", refs: ["core:px"] },
    ],
    conditions_not_met: [],
    conditions_inconclusive_or_unsupported: [],
    evidence_support: [
      { text: "Regime evidence.", refs: ["regime:primary"] },
    ],
    data_quality_notes: ["59 closed candles were used."],
    invalidation_notes: [],
    uncertainty: ["Regime classification is a heuristic."],
    suggested_observations: ["Watch further closed candles."],
    unavailable_information: ["No tick data is part of the report."],
  },
  validation: {
    schema_ok: true,
    evidence_refs_ok: true,
    numeric_trace_ok: true,
    safety_ok: true,
    checks: [],
    checked_refs: 2,
  },
  provider: {
    name: "scripted_fake",
    model: "fake-model-1",
    elapsed_seconds: 0.4,
    attempts: 1,
  },
  generated_at: "2026-01-01T01:00:00.000+00:00",
  ai_layer_version: "1.0.0",
  note: "AI-generated interpretation.",
};

const conversationsPage: PaginatedAIConversations = {
  items: [
    {
      id: "cccccccc-1111-1111-1111-111111111111",
      title: "Condition research",
      analysis_id: "aaaaaaaa-1111-1111-1111-111111111111",
      backtest_id: null,
      research_id: null,
      message_count: 2,
      last_message_at: "2026-01-01T01:05:00.000+00:00",
      created_at: "2026-01-01T01:00:00.000+00:00",
      updated_at: "2026-01-01T01:05:00.000+00:00",
    },
  ],
  page: { page: 1, page_size: 50, total: 1, pages: 1 },
};

const chatResponse: AIChatResponse = {
  conversation: conversationsPage.items[0],
  messages: [
    {
      id: "dddddddd-1111-1111-1111-111111111111",
      conversation_id: "cccccccc-1111-1111-1111-111111111111",
      seq: 1,
      role: "user",
      content: "Why did my strategy fail?",
      validation: null,
      provider: null,
      model: null,
      refs: null,
      created_at: "2026-01-01T01:00:00.000+00:00",
    },
    {
      id: "dddddddd-2222-2222-2222-222222222222",
      conversation_id: "cccccccc-1111-1111-1111-111111111111",
      seq: 2,
      role: "assistant",
      content: "The recorded evaluation shows the core condition was not met.",
      validation: {
        schema_ok: true,
        evidence_refs_ok: true,
        numeric_trace_ok: true,
        safety_ok: true,
        checks: [],
        checked_refs: 1,
      },
      provider: "scripted_fake",
      model: "fake-model-1",
      refs: null,
      created_at: "2026-01-01T01:00:01.000+00:00",
    },
  ],
  refs: [{ kind: "analysis", id: "aaaaaaaa-1111-1111-1111-111111111111" }],
  ai_layer_version: "1.0.0",
  note: "AI-generated interpretation.",
};

const conversationDetail: AIConversationDetail = {
  conversation: conversationsPage.items[0],
  messages: chatResponse.messages,
};

function analysisReportsPage(): PaginatedAnalysisReports {
  return {
    items: [
      {
        id: "aaaaaaaa-1111-1111-1111-111111111111",
        symbol: "EURUSD",
        mode: "forex",
        strategy_id: "bbbbbbbb-1111-1111-1111-111111111111",
        strategy_name: "Trend pullback",
        strategy_version: 3,
        setup_status: "conditions_missing",
        direction: "bullish",
        regime: "range",
        report_schema_version: "1.0",
        engine_version: "4.0.0",
        evaluated_at: "2026-01-01T00:00:00.000+00:00",
        created_at: "2026-01-01T00:00:00.000+00:00",
      },
    ],
    page: { page: 1, page_size: 20, total: 1, pages: 1 },
  } as unknown as PaginatedAnalysisReports;
}

const backtestRunsPage: PaginatedBacktestRuns = {
  items: [],
  page: { page: 1, page_size: 20, total: 0, pages: 1 },
};

function mockBackend(overrides: {
  status?: AIStatus;
  statusError?: unknown;
  explanation?: AIExplanationResponse | unknown;
  explanationError?: unknown;
  conversations?: PaginatedAIConversations;
  chat?: AIChatResponse;
  chatError?: unknown;
} = {}) {
  getMock.mockImplementation((path: string) => {
    if (path.startsWith("/api/v1/ai/status")) {
      if (overrides.statusError) return Promise.reject(overrides.statusError);
      return Promise.resolve(overrides.status ?? statusConfigured);
    }
    if (path.startsWith("/api/v1/ai/research/conversations/")) {
      return Promise.resolve(conversationDetail);
    }
    if (path.startsWith("/api/v1/ai/research/conversations")) {
      return Promise.resolve(overrides.conversations ?? conversationsPage);
    }
    if (path.startsWith("/api/v1/analysis/reports")) {
      return Promise.resolve(analysisReportsPage());
    }
    if (path.startsWith("/api/v1/backtests")) {
      return Promise.resolve(backtestRunsPage);
    }
    return Promise.reject(new ApiError({ code: "NOT_FOUND", message: path, status: 404 }));
  });
  postMock.mockImplementation((path: string) => {
    if (path.startsWith("/api/v1/ai/explanations")) {
      if (overrides.explanationError) return Promise.reject(overrides.explanationError);
      return Promise.resolve(overrides.explanation ?? explanationResponse);
    }
    if (path.startsWith("/api/v1/ai/research/chat")) {
      if (overrides.chatError) return Promise.reject(overrides.chatError);
      return Promise.resolve(overrides.chat ?? chatResponse);
    }
    return Promise.reject(new ApiError({ code: "NOT_FOUND", message: path, status: 404 }));
  });
}

// ---------------------------------------------------------------------------
// AI explanation panel
// ---------------------------------------------------------------------------

describe("AIExplanationPanel", () => {
  it("explains why AI is unavailable when the provider is not configured", async () => {
    mockBackend({ status: statusUnconfigured });
    render(<AIExplanationPanel analysisId="aaaaaaaa-1111-1111-1111-111111111111" />);
    const note = await screen.findByTestId("ai-unconfigured");
    expect(note.textContent).toContain("provider is not configured");
    expect(note.textContent).toContain("remains fully usable");
    // No explain button while unconfigured.
    expect(screen.queryByRole("button", { name: /explain this report/i })).toBeNull();
  });

  it("shows a loading state and then the validated explanation with evidence refs", async () => {
    let release!: (v: AIExplanationResponse) => void;
    const gate = new Promise<AIExplanationResponse>((resolve) => {
      release = resolve;
    });
    mockBackend();
    postMock.mockImplementation((path: string) => {
      if (path.startsWith("/api/v1/ai/explanations")) return gate;
      return Promise.reject(new ApiError({ code: "NOT_FOUND", message: path, status: 404 }));
    });
    const user = userEvent.setup();
    render(<AIExplanationPanel analysisId="aaaaaaaa-1111-1111-1111-111111111111" />);

    const button = await screen.findByRole("button", { name: /explain this report with ai/i });
    await user.click(button);
    expect(screen.getByTestId("ai-loading")).toBeTruthy();

    release(explanationResponse);
    await waitFor(() => expect(screen.getByTestId("ai-explanation")).toBeTruthy());
    expect(screen.getAllByText(/AI-generated interpretation/i).length).toBeGreaterThan(0);
    // Evidence refs are visible as deterministic checkables (open sections).
    expect(screen.getAllByText("core:px").length).toBeGreaterThan(0);
    // Validation metadata (evidence refs checked).
    expect(screen.getByText(/2 evidence refs checked/i)).toBeTruthy();
    // The honest limits sentence is shown.
    expect(screen.getByText(/not factual market truth/i)).toBeTruthy();
  });

  it("renders the fail-closed error state with retry", async () => {
    mockBackend({
      explanationError: new ApiError({
        code: "AI_OUTPUT_INVALID",
        message: "The AI explanation violated output policy and was discarded.",
        status: 502,
      }),
    });
    const user = userEvent.setup();
    render(<AIExplanationPanel analysisId="aaaaaaaa-1111-1111-1111-111111111111" />);
    await user.click(await screen.findByRole("button", { name: /explain this report with ai/i }));

    const err = await screen.findByTestId("ai-error");
    expect(err.textContent).toContain("violated output policy");
    expect(err.textContent).toContain("fail-closed");
    expect(screen.getByRole("button", { name: /try again/i })).toBeTruthy();
  });

  it("handles a status fetch failure honestly without blocking", async () => {
    mockBackend({ statusError: new ApiError({ code: "BACKEND_UNREACHABLE", message: "Backend down.", status: 0 }) });
    render(<AIExplanationPanel analysisId={null} />);
    const note = await screen.findByRole("alert");
    expect(note.textContent).toContain("AI status unavailable");
    expect(note.textContent).toContain("remains fully usable");
  });

  it("posts the analysis id and keeps the deterministic report authoritative", async () => {
    mockBackend();
    const user = userEvent.setup();
    render(
      <AIExplanationPanel
        analysisId="aaaaaaaa-1111-1111-1111-111111111111"
        reportLabel="EURUSD · Trend pullback (v3)"
      />,
    );
    await user.click(await screen.findByRole("button", { name: /explain this report with ai/i }));
    await waitFor(() => expect(screen.getByTestId("ai-explanation")).toBeTruthy());
    expect(postMock).toHaveBeenCalledWith("/api/v1/ai/explanations", {
      analysis_id: "aaaaaaaa-1111-1111-1111-111111111111",
    });
    expect(screen.getByText(/EURUSD · Trend pullback \(v3\)/)).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// Research chat
// ---------------------------------------------------------------------------

describe("ResearchChat", () => {
  it("shows the empty state with research suggestions when no conversation is open", async () => {
    mockBackend({ conversations: { items: [], page: { page: 1, page_size: 50, total: 0, pages: 1 } } });
    render(<ResearchChat mode="forex" />);
    const empty = await screen.findByTestId("chat-empty");
    expect(empty.textContent).toContain("Attach a saved analysis");
    expect(empty.textContent).toContain("Why did my selected strategy fail its conditions?");
    expect(screen.getByTestId("conversations-empty")).toBeTruthy();
  });

  it("shows provider-not-configured guidance in the chat thread", async () => {
    mockBackend({ status: statusUnconfigured });
    render(<ResearchChat mode="forex" />);
    const empty = await screen.findByTestId("chat-empty");
    await waitFor(() => expect(empty.textContent).toContain("provider is not configured"));
    expect(screen.getByLabelText("Research question")).toBeDisabled();
  });

  it("sends a question with the attached context and renders both messages", async () => {
    mockBackend();
    const user = userEvent.setup();
    render(<ResearchChat mode="forex" />);

    // Attach the analysis report shown in the selector.
    const trigger = await screen.findByLabelText("Attach analysis report");
    await user.click(trigger);
    await user.click(await screen.findByText(/EURUSD · Trend pullback \(v3\)/));

    const input = screen.getByLabelText("Research question");
    await user.type(input, "Why did my strategy fail?{Enter}");

    await waitFor(() => expect(screen.getByTestId("chat-message-user")).toBeTruthy());
    expect(screen.getByTestId("chat-message-assistant")).toBeTruthy();
    expect(screen.getByTestId("chat-message-assistant").textContent).toContain(
      "core condition was not met",
    );
    expect(screen.getByText(/evidence refs checked: 1/i)).toBeTruthy();
    // The conversation context line is visible (artefact + count).
    expect(screen.getByTestId("conversation-context").textContent).toMatch(/2 messages/);
    expect(postMock).toHaveBeenCalledWith(
      "/api/v1/ai/research/chat",
      expect.objectContaining({
        message: "Why did my strategy fail?",
        analysis_id: "aaaaaaaa-1111-1111-1111-111111111111",
      }),
    );
  });

  it("surfaces errors without losing the conversation state", async () => {
    mockBackend({
      chatError: new ApiError({ code: "AI_PROVIDER_TIMEOUT", message: "Provider timed out.", status: 502 }),
    });
    const user = userEvent.setup();
    render(<ResearchChat mode="forex" />);
    const input = await screen.findByLabelText("Research question");
    await user.type(input, "hello{Enter}");
    await waitFor(() => expect(toastMock).toHaveBeenCalled());
    const toastArg = toastMock.mock.calls[0][0] as { title: string };
    expect(toastArg.title).toMatch(/failed/i);
  });

  it("lists and opens existing conversations, then deletes on request", async () => {
    mockBackend();
    const user = userEvent.setup();
    render(<ResearchChat mode="forex" />);

    await waitFor(() => expect(screen.getByTestId("conversation-list")).toBeTruthy());
    await user.click(screen.getByText("Condition research"));
    await waitFor(() => expect(screen.getByTestId("chat-message-user")).toBeTruthy());

    await user.click(screen.getByRole("button", { name: /delete conversation condition research/i }));
    await waitFor(() =>
      expect(deleteMock).toHaveBeenCalledWith(
        "/api/v1/ai/research/conversations/cccccccc-1111-1111-1111-111111111111",
      ),
    );
  });

  it("can start a new research conversation", async () => {
    mockBackend();
    const user = userEvent.setup();
    render(<ResearchChat mode="forex" />);
    await user.click(await screen.findByRole("button", { name: /new/i }));
    expect(screen.getByTestId("chat-empty")).toBeTruthy();
  });
});
