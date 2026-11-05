import {
  expect,
  request as playwrightRequest,
  test,
  type APIRequestContext,
  type APIResponse,
  type Page,
} from "@playwright/test";

const E2E_ENABLED = process.env.RUN_E2E === "1";
const API_BASE =
  process.env.PLAYWRIGHT_API_URL ?? "http://127.0.0.1:8000";

type Candle = { time: number; close: number | string };
type Seed = {
  suffix: string;
  strategyId: string;
  strategyName: string;
  version: number;
  analysisId: string;
  backtestId: string;
  researchId: string;
  replayStart: string;
  replayEnd: string;
  windowCandleUpperBound: number;
};

let seed: Seed | undefined;

function requiredSeed(): Seed {
  if (!seed) {
    throw new Error("E2E seed was not created");
  }
  return seed;
}

async function data<T>(response: APIResponse): Promise<T> {
  if (!response.ok()) {
    throw new Error(
      `${response.url()} -> ${response.status()}: ${(await response.text()).slice(0, 500)}`,
    );
  }
  return (await response.json()).data as T;
}

async function waitForRun(
  api: APIRequestContext,
  path: string,
  timeoutMs = 180_000,
): Promise<{ status: string }> {
  const deadline = Date.now() + timeoutMs;
  let last: { status: string } = { status: "unknown" };
  while (Date.now() < deadline) {
    last = await data<{ status: string }>(await api.get(path));
    if (["completed", "failed", "cancelled"].includes(last.status)) {
      return last;
    }
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  throw new Error(`${path} did not finish: ${last.status}`);
}

function iso(epochSeconds: number): string {
  return new Date(epochSeconds * 1000).toISOString();
}

function toDatetimeLocal(isoValue: string): string {
  const date = new Date(isoValue);
  const pad = (value: number) => String(value).padStart(2, "0");
  return (
    `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}` +
    `T${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}`
  );
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

async function openTab(page: Page, name: string) {
  await page.getByRole("tab", { name, exact: true }).click();
  const panel = page.locator('[role="tabpanel"]:visible').last();
  await expect(panel).toBeVisible();
  return panel;
}

test.describe("Phase 10 browser workflows", () => {
  test.skip(
    !E2E_ENABLED,
    "Set RUN_E2E=1 with PostgreSQL, the backend on :8000, and the frontend on :3000 running.",
  );
  test.describe.configure({ mode: "serial" });

  test.beforeAll(async () => {
    if (!E2E_ENABLED) return;
    const api = await playwrightRequest.newContext({ baseURL: API_BASE });
    try {
      const suffix = Date.now().toString(36);
      const candles = (
        await data<{ candles: Candle[] }>(
          await api.get("/api/v1/market/candles", {
            params: { symbol: "EURUSD", timeframe: "M1", limit: 500 },
          }),
        )
      ).candles;
      expect(candles.length).toBeGreaterThanOrEqual(400);
      const lastTime = candles[candles.length - 1].time;
      const closes = candles.map((candle) => Number(candle.close));
      const threshold = Number((Math.min(...closes) - 0.01).toFixed(5));
      const window = {
        start: iso(lastTime - 399 * 60),
        end: iso(lastTime + 60),
      };

      const category = await data<{ id: string }>(
        await api.post("/api/v1/strategy-categories", {
          data: { name: `E2E browser category ${suffix}`, mode: "forex" },
        }),
      );
      const strategyName = `E2E browser strategy ${suffix}`;
      const strategy = await data<{ id: string }>(
        await api.post("/api/v1/strategies", {
          data: {
            name: strategyName,
            mode: "forex",
            direction: "bullish",
            category_id: category.id,
            description: "Automated browser E2E fixture",
          },
        }),
      );
      const ruleSet = {
        schema_version: "2.0",
        mode: "forex",
        timeframes: ["M1"],
        direction: "bullish",
        conditions: {
          logic: "AND",
          conditions: [
            {
              id: "px",
              type: "price_level",
              timeframe: "M1",
              lookback: 20,
              params: {
                field: "close",
                operator: "above",
                value: threshold,
              },
            },
          ],
          groups: [],
        },
      };
      const version = await data<{ version: number }>(
        await api.post(`/api/v1/strategies/${strategy.id}/versions`, {
          data: { rule_set: ruleSet, changelog: "Browser E2E fixture" },
        }),
      );
      await data(
        await api.post(
          `/api/v1/strategies/${strategy.id}/versions/${version.version}/publish`,
        ),
      );

      const analysis = await data<{ analysis_id: string }>(
        await api.post("/api/v1/analysis/run", {
          data: { strategy_id: strategy.id, symbol: "EURUSD" },
        }),
      );

      const backtest = await data<{ id: string }>(
        await api.post("/api/v1/backtests", {
          data: {
            mode: "forex",
            strategy_id: strategy.id,
            strategy_version: version.version,
            symbol: "EURUSD",
            ...window,
          },
        }),
      );
      const backtestFinal = await waitForRun(
        api,
        `/api/v1/backtests/${backtest.id}`,
      );
      expect(backtestFinal.status).toBe("completed");

      const research = await data<{ id: string }>(
        await api.post("/api/v1/research/optimization", {
          data: {
            mode: "forex",
            strategy_id: strategy.id,
            strategy_version: version.version,
            symbol: "EURUSD",
            ...window,
            parameters: [
              {
                condition_id: "px",
                field: "lookback",
                values: [10, 20],
              },
            ],
            objective: { metric: "conditions_met_count" },
            validation_mode: "holdout",
          },
        }),
      );
      const researchFinal = await waitForRun(
        api,
        `/api/v1/research/optimization/${research.id}`,
      );
      expect(researchFinal.status).toBe("completed");

      seed = {
        suffix,
        strategyId: strategy.id,
        strategyName,
        version: version.version,
        analysisId: analysis.analysis_id,
        backtestId: backtest.id,
        researchId: research.id,
        replayStart: window.start,
        replayEnd: window.end,
        windowCandleUpperBound: 400,
      };
    } finally {
      await api.dispose();
    }
  });

  test("startup renders the shell and live market data without page errors", async ({
    page,
  }) => {
    const pageErrors: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await page.goto("/", { waitUntil: "networkidle" });
    await expect(page).toHaveTitle(/TA Workbench/);
    await expect(
      page.getByRole("tab", { name: "Dashboard", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("tab", { name: "Research Lab", exact: true }),
    ).toBeVisible();
    await expect(page.getByText("Live data").first()).toBeVisible();
    await expect(page.getByText(/EURUSD.*M1/).first()).toBeVisible();
    await expect(page.getByText("data: ok").first()).toBeVisible();
    expect(pageErrors).toEqual([]);
  });

  test("strategy library lists the published fixture", async ({ page }) => {
    const fixture = requiredSeed();
    await page.goto("/", { waitUntil: "networkidle" });
    const panel = await openTab(page, "Strategies");
    await expect(
      panel.getByRole("button", {
        name: `Open builder for ${fixture.strategyName}`,
        exact: true,
      }),
    ).toBeVisible();
    await expect(
      panel.getByText(`pub v${fixture.version}`, { exact: true }).first(),
    ).toBeVisible();
    await expect(
      panel.getByText("active", { exact: true }).first(),
    ).toBeVisible();
  });

  test("analysis runs from the UI and opens a persisted report", async ({
    page,
  }) => {
    const fixture = requiredSeed();
    await page.goto("/", { waitUntil: "networkidle" });
    const panel = await openTab(page, "Analysis");

    const combo = panel.getByRole("combobox").first();
    await combo.click();
    await page
      .getByRole("option", {
        name: new RegExp(escapeRegExp(fixture.strategyName)),
      })
      .click();

    const responsePromise = page.waitForResponse(
      (response) =>
        response.url().endsWith("/api/v1/analysis/run") &&
        response.request().method() === "POST",
    );
    await panel.getByRole("button", { name: "Run analysis" }).click();
    const response = await responsePromise;
    expect(response.status()).toBe(201);
    const body = (await response.json()) as {
      data?: { analysis_id?: string };
    };
    expect(body.data?.analysis_id).toBeTruthy();

    await expect(panel.getByText("EURUSD").first()).toBeVisible();
    const viewReport = panel.getByRole("button", { name: "View report" }).first();
    await expect(viewReport).toBeVisible();
    await viewReport.click();
    await expect(
      page.getByText(/limitations/i).first(),
    ).toBeVisible();
    await expect(
      page.getByText(/data provenance|provenance/i).first(),
    ).toBeVisible();
  });

  test("backtest history shows the completed setup-only run", async ({
    page,
  }) => {
    const fixture = requiredSeed();
    await page.goto("/", { waitUntil: "networkidle" });
    const panel = await openTab(page, "Backtests");
    await expect(panel.getByText(fixture.strategyName).first()).toBeVisible();
    await expect(panel.getByText(/EURUSD.*M1/).first()).toBeVisible();
    await expect(panel.getByText("Completed").first()).toBeVisible();
    await expect(panel.getByText("N/A").first()).toBeVisible();
    await expect(
      panel.getByRole("button", { name: "Start backtest" }),
    ).toBeEnabled();
  });

  test("research history shows ranked candidates", async ({ page }) => {
    const fixture = requiredSeed();
    await page.goto("/", { waitUntil: "networkidle" });
    const panel = await openTab(page, "Research Lab");
    await expect(panel.getByText(fixture.strategyName).first()).toBeVisible();
    await expect(panel.getByText("Completed").first()).toBeVisible();
    await expect(panel.getByText("3/3").first()).toBeVisible();
    await expect(
      panel.getByText("conditions_met_count").first(),
    ).toBeVisible();
  });

  test("replay creates a session, steps the cursor, and resumes after reload", async ({
    page,
  }) => {
    const fixture = requiredSeed();
    await page.goto("/", { waitUntil: "networkidle" });
    let panel = await openTab(page, "Replay");

    await expect(panel.getByText("New replay session")).toBeVisible();
    await panel.getByLabel("Replay timeframe").selectOption("M1");
    await panel
      .locator("#replay-start")
      .fill(toDatetimeLocal(fixture.replayStart));
    await panel.locator("#replay-end").fill(toDatetimeLocal(fixture.replayEnd));

    await panel.getByRole("button", { name: "Check stored data" }).click();
    await expect(panel.getByTestId("replay-coverage")).toBeVisible();
    await expect(
      panel.getByText(/Closed candles/i).first(),
    ).toBeVisible();

    const createResponse = page.waitForResponse(
      (response) =>
        response.url().endsWith("/api/v1/replay/sessions") &&
        response.request().method() === "POST",
    );
    await panel.getByRole("button", { name: "Create replay" }).click();
    const created = (await (await createResponse).json()) as {
      data?: { id?: string };
    };
    const replayId = created.data?.id;
    expect(replayId).toBeTruthy();

    await expect(panel.getByText(/Historical replay/).first()).toBeVisible();
    await expect(panel.getByText("Paused", { exact: true }).first()).toBeVisible();

    const visible = panel.getByTestId("replay-visible-count");
    const initial = Number(
      ((await visible.textContent()) ?? "").replace(/,/g, "").match(/\d+/)?.[0],
    );
    expect(initial).toBeGreaterThan(0);
    expect(initial).toBeLessThanOrEqual(fixture.windowCandleUpperBound);

    await panel
      .getByRole("button", { name: "Step forward one candle" })
      .click();
    await expect
      .poll(async () => {
        const text = ((await visible.textContent()) ?? "").replace(/,/g, "");
        return Number(text.match(/\d+/)?.[0]);
      })
      .toBe(initial + 1);

    await page.reload({ waitUntil: "networkidle" });
    panel = await openTab(page, "Replay");
    await expect(panel.getByTestId("replay-history-table")).toBeVisible();
    await panel.getByText(/EURUSD.*M1/).first().click();

    await expect(panel.getByText(/Historical replay/).first()).toBeVisible();
    await expect
      .poll(async () => {
        const text = ((await visible.textContent()) ?? "").replace(/,/g, "");
        return Number(text.match(/\d+/)?.[0]);
      })
      .toBe(initial + 1);

    if (replayId) {
      const api = await playwrightRequest.newContext({ baseURL: API_BASE });
      try {
        const response = await api.delete(`/api/v1/replay/sessions/${replayId}`);
        expect(response.ok()).toBeTruthy();
      } finally {
        await api.dispose();
      }
    }
  });

  test("persisted results survive a full browser reload", async ({ page }) => {
    const fixture = requiredSeed();
    await page.goto("/", { waitUntil: "networkidle" });
    await page.reload({ waitUntil: "networkidle" });

    for (const [tab, expected] of [
      ["Strategies", fixture.strategyName],
      ["Backtests", fixture.strategyName],
      ["Research Lab", fixture.strategyName],
    ] as const) {
      const panel = await openTab(page, tab);
      await expect(panel.getByText(expected).first()).toBeVisible();
    }
  });

  test("AI research reports the disabled provider honestly", async ({
    page,
  }) => {
    await page.goto("/", { waitUntil: "networkidle" });
    const panel = await openTab(page, "AI Research");
    await expect(
      panel.getByText(/AI research is unavailable/i),
    ).toBeVisible();
    await expect(
      panel.getByText(/provider is not configured/i),
    ).toBeVisible();
  });
});
