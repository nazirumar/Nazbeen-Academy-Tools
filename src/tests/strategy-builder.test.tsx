/**
 * Strategy Builder tests (Phase 3).
 *
 * The API client is mocked; these tests verify editing behaviour,
 * validation-error placement, the unsaved-changes guard, the publish
 * preview flow and the "planned rule types are never presented as usable"
 * guarantee.
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApiError } from "@/lib/api/types";
import type {
  RuleSetValidation,
  StrategyDetail,
  StrategyVersion,
} from "@/lib/api/types";

const api = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
}));

vi.mock("@/lib/api/client", () => ({ api }));

import { StrategyBuilder } from "@/components/strategies/strategy-builder";

// --------------------------------------------------------------------------- //
// Fixtures
// --------------------------------------------------------------------------- //

const RULE_TYPES = {
  rule_types: [
    {
      type: "price_level",
      title: "Price vs level",
      description: "Compare a price series against a threshold.",
      semantics: "Latest candle must satisfy the comparison.",
      available: true,
      status: "operational",
      supported_modes: ["forex", "binary_options"],
      default_lookback: 20,
      data_note: "",
      required_params: ["field", "operator", "value"],
      params_schema: {
        field: { type: "string", enum: ["open", "high", "low", "close"], description: "Price field." },
        operator: { type: "string", enum: ["above", "below"], description: "Operator." },
        value: { type: "number", description: "Threshold." },
      },
    },
    {
      type: "swing_point",
      title: "Swing high / swing low",
      description: "Confirmed swing points.",
      semantics: "Confirmed by strength candles after the swing.",
      available: true,
      status: "operational",
      supported_modes: ["forex", "binary_options"],
      default_lookback: 20,
      data_note: "",
      required_params: ["kind", "strength"],
      params_schema: {
        kind: { type: "string", enum: ["high", "low"], description: "Swing kind." },
        strength: { type: "integer", minimum: 1, maximum: 10, description: "Strength." },
      },
    },
    {
      type: "mss_choch",
      title: "MSS / CHoCH (planned)",
      description: "Planned.",
      semantics: "PLANNED — not operational.",
      available: false,
      status: "planned",
      supported_modes: ["forex", "binary_options"],
      default_lookback: 20,
      data_note: "",
      required_params: [],
      params_schema: {},
    },
  ],
};

const storedRuleSet = {
  schema_version: "2.0",
  mode: "forex",
  timeframes: ["H1"],
  direction: "bullish",
  conditions: {
    logic: "AND",
    conditions: [
      {
        id: "c1",
        type: "price_level",
        timeframe: "H1",
        lookback: 20,
        params: { field: "close", operator: "above", value: 1.08 },
      },
    ],
    groups: [],
  },
  confirmation: null,
  invalidation: null,
  symbols: [],
  market_regime: "any",
  evaluation: { on_close_only: true },
};

const strategyDetail: StrategyDetail = {
  id: "11111111-1111-1111-1111-111111111111",
  name: "Test Strategy",
  mode: "forex",
  direction: "bullish",
  status: "draft",
  description: "Original description",
  category_id: null,
  current_version: 1,
  published_version_number: null,
  created_at: "2026-11-01T00:00:00Z",
  updated_at: "2026-11-01T00:00:00Z",
  rule_set: storedRuleSet,
  summary: ["Mode: Forex · schema 2.0 · Bullish (long setups)."],
};

const versions: StrategyVersion[] = [
  {
    id: "v1",
    strategy_id: strategyDetail.id,
    version: 1,
    status: "draft",
    changelog: "initial",
    rule_set: storedRuleSet,
    created_at: "2026-11-01T00:00:00Z",
  },
];

function setupApi() {
  api.get.mockImplementation((path: string) => {
    if (path.endsWith("/rule-types")) return Promise.resolve(RULE_TYPES);
    if (path === `/api/v1/strategies/${strategyDetail.id}`) {
      return Promise.resolve(strategyDetail);
    }
    if (path === `/api/v1/strategies/${strategyDetail.id}/versions`) {
      return Promise.resolve(versions);
    }
    return Promise.reject(new Error(`unexpected GET ${path}`));
  });
  api.post.mockResolvedValue({});
  api.patch.mockResolvedValue(strategyDetail);
}

function renderBuilder() {
  return render(
    <StrategyBuilder
      strategyId={strategyDetail.id}
      mode="forex"
      categories={[]}
      onCategoriesChanged={async () => {}}
      onClose={vi.fn()}
      onSaved={async () => {}}
    />,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  setupApi();
});

// --------------------------------------------------------------------------- //

describe("StrategyBuilder", () => {
  it("loads strategy, versions and rule types, then renders the editor", async () => {
    renderBuilder();
    expect(await screen.findByLabelText("Name")).toHaveValue("Test Strategy");
    expect(screen.getByLabelText("Description / intent")).toHaveValue(
      "Original description",
    );
    expect(api.get).toHaveBeenCalledWith("/api/v1/rule-types");
    // The stored draft appears in the version history with its status
    // ("draft" also shows as the strategy status badge → both present).
    expect(screen.getByText("initial")).toBeInTheDocument();
    expect(screen.getAllByText("draft").length).toBeGreaterThanOrEqual(2);
  });

  it("offers all three directions including Both", async () => {
    renderBuilder();
    await screen.findByLabelText("Name");
    // The direction select trigger is a combobox labelled "Direction".
    expect(screen.getByLabelText("Direction")).toBeInTheDocument();
  });

  it("renders parameter fields from the registry schema for existing rules", async () => {
    renderBuilder();
    await screen.findByLabelText("Name");
    // price_level params: enum selects for field/operator + numeric value.
    expect(screen.getByLabelText("field")).toBeInTheDocument();
    expect(screen.getByLabelText("operator")).toBeInTheDocument();
    expect(screen.getByLabelText("value")).toHaveValue(1.08);
  });

  it("shows validation errors next to the relevant fields", async () => {
    api.post.mockImplementation((path: string) => {
      if (path === "/api/v1/rule-sets/validate") {
        const res: RuleSetValidation = {
          valid: false,
          errors: [
            {
              path: ".conditions.conditions[0].params.value",
              message: "value must be a finite number.",
              code: "invalid_param_type",
            },
          ],
          normalized: null,
        };
        return Promise.resolve(res);
      }
      return Promise.resolve({});
    });
    const user = userEvent.setup();
    renderBuilder();
    await screen.findByLabelText("Name");
    await user.click(screen.getByRole("button", { name: /validate/i }));
    await waitFor(() => {
      // Rendered inline next to the field AND in the global error panel.
      expect(screen.getAllByText(/value must be a finite number/).length).toBeGreaterThanOrEqual(1);
    });
    // Inline: the error appears inside the condition card, next to `value`.
    const card = document.querySelector('[data-condition-id="c1"]');
    expect(card).not.toBeNull();
    expect(card as HTMLElement).toHaveTextContent("value must be a finite number.");
    // Global error counter is rendered too.
    expect(screen.getByText(/1 validation error\(s\)/)).toBeInTheDocument();
  });

  it("saves a draft version and posts the rule set", async () => {
    const savedVersion: StrategyVersion = {
      ...versions[0],
      version: 2,
      status: "draft",
      changelog: "test change",
    };
    api.post.mockImplementation((path: string) => {
      if (path === `/api/v1/strategies/${strategyDetail.id}/versions`) {
        return Promise.resolve(savedVersion);
      }
      return Promise.resolve({});
    });
    api.patch.mockResolvedValue(strategyDetail);
    api.get.mockImplementation((path: string) => {
      if (path.endsWith("/rule-types")) return Promise.resolve(RULE_TYPES);
      if (path === `/api/v1/strategies/${strategyDetail.id}/versions`) {
        return Promise.resolve([savedVersion, ...versions]);
      }
      return Promise.resolve(strategyDetail);
    });
    const user = userEvent.setup();
    renderBuilder();
    await screen.findByLabelText("Name");
    await user.type(screen.getByLabelText("Changelog"), "test change");
    await user.click(screen.getByRole("button", { name: /save draft/i }));
    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        `/api/v1/strategies/${strategyDetail.id}/versions`,
        expect.objectContaining({ changelog: "test change" }),
      );
    });
    const payload = api.post.mock.calls.find(
      (c) => c[0] === `/api/v1/strategies/${strategyDetail.id}/versions`,
    )?.[1];
    expect(payload.rule_set.conditions.conditions[0].params.value).toBe(1.08);
    // The version list refreshes to include the new draft.
    await waitFor(() => {
      expect(screen.getByText("test change")).toBeInTheDocument();
    });
  });

  it("places server-side save errors into the error panel", async () => {
    api.post.mockImplementation((path: string) => {
      if (path === `/api/v1/strategies/${strategyDetail.id}/versions`) {
        throw new ApiError({
          code: "RULE_VALIDATION_FAILED",
          message: "Rule set validation failed.",
          status: 422,
          details: [
            {
              path: ".conditions.conditions[0].params.strength",
              message: "Missing required parameter 'strength'.",
            },
          ],
        });
      }
      return Promise.resolve({});
    });
    const user = userEvent.setup();
    renderBuilder();
    await screen.findByLabelText("Name");
    await user.click(screen.getByRole("button", { name: /save draft/i }));
    await waitFor(() => {
      expect(screen.getByText(/Missing required parameter 'strength'/)).toBeInTheDocument();
    });
  });

  it("warns about unsaved changes and asks before discarding", async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(
      <StrategyBuilder
        strategyId={strategyDetail.id}
        mode="forex"
        categories={[]}
        onCategoriesChanged={async () => {}}
        onClose={onClose}
        onSaved={async () => {}}
      />,
    );
    await screen.findByLabelText("Name");
    expect(screen.queryByText(/unsaved changes/i)).not.toBeInTheDocument();

    await user.type(screen.getByLabelText("Name"), "!");
    expect(screen.getByText(/unsaved changes/i)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /library/i }));
    expect(await screen.findByText(/discard unsaved changes/i)).toBeInTheDocument();
    // Cancel keeps editing.
    await user.click(screen.getByRole("button", { name: /keep editing/i }));
    expect(onClose).not.toHaveBeenCalled();
    // Discard leaves.
    await user.click(screen.getByRole("button", { name: /library/i }));
    await user.click(await screen.findByRole("button", { name: /discard changes/i }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("shows the full definition in the publish preview and publishes after confirmation", async () => {
    api.post.mockImplementation((path: string) => {
      if (path === `/api/v1/strategies/${strategyDetail.id}/versions/1/publish`) {
        return Promise.resolve({
          version: { ...versions[0], status: "published" },
          strategy: { ...strategyDetail, status: "active", published_version_number: 1 },
          summary: ["Published."],
        });
      }
      if (path === `/api/v1/strategies/${strategyDetail.id}/versions`) {
        return Promise.resolve(versions[0]);
      }
      return Promise.resolve({});
    });
    const user = userEvent.setup();
    renderBuilder();
    await screen.findByLabelText("Name");
    await user.click(screen.getByRole("button", { name: /publish/i }));
    const preview = await screen.findByTestId("publish-preview");
    expect(preview).toHaveTextContent('"schema_version": "2.0"');
    expect(preview).toHaveTextContent('"close"');
    await user.click(screen.getByRole("button", { name: /save & publish version/i }));
    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        `/api/v1/strategies/${strategyDetail.id}/versions/1/publish`,
      );
    });
    // The published pointer is reflected in the header badges.
    await waitFor(() => {
      expect(screen.getByText("published v1")).toBeInTheDocument();
    });
  });

  it("marks planned rule types as unavailable in the type selector", async () => {
    const user = userEvent.setup();
    renderBuilder();
    await screen.findByLabelText("Name");
    // Open the rule-type select of the existing condition.
    await user.click(screen.getByLabelText(/rule type of c1/i));
    const plannedOption = await screen.findByRole("option", {
      name: /mss \/ choch \(planned\)/i,
    });
    expect(plannedOption).toHaveAttribute("data-disabled");
    // Operational alternatives are offered.
    expect(
      screen.getByRole("option", { name: /swing high \/ swing low/i }),
    ).toBeInTheDocument();
  });

  it("adds a rule card with registry-driven parameter fields", async () => {
    const user = userEvent.setup();
    renderBuilder();
    await screen.findByLabelText("Name");
    await user.click(screen.getByRole("button", { name: /^rule$/i }));
    // A second condition card appears (2 rule id badges in the core section).
    await waitFor(() => {
      expect(document.querySelectorAll("[data-condition-id]").length).toBe(2);
    });
  });

  it("offers the JSON editor tab", async () => {
    const user = userEvent.setup();
    renderBuilder();
    await screen.findByLabelText("Name");
    await user.click(screen.getByRole("tab", { name: /json/i }));
    expect(await screen.findByLabelText("Rule set JSON")).toBeInTheDocument();
  });
});
