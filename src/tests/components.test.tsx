import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { EvaluationTree } from "@/components/analysis/analysis-workbench";
import { ModeSwitch } from "@/components/layout/mode-switch";
import type { EvaluationNode } from "@/lib/api/types";

const leaf = (id: string, satisfied: boolean): EvaluationNode => ({
  kind: "condition",
  id,
  satisfied,
  description: `${id} description`,
  evidence: {
    timeframe: "M1",
    candles_evaluated: 5,
    current_value: 101.5,
    threshold: 100,
    criterion: "latest candle must satisfy the comparison",
  },
});

const group: EvaluationNode = {
  kind: "group",
  id: "group_and",
  logic: "AND",
  satisfied: false,
  description: "ALL of 2 condition(s)/group(s) must hold",
  evidence: {},
  children: [leaf("c1", true), leaf("c2", false)],
};

describe("EvaluationTree (explainability)", () => {
  it("renders group logic badge and both children", () => {
    render(<EvaluationTree node={group} />);
    expect(screen.getByText("AND")).toBeInTheDocument();
    expect(screen.getByText("c1 description")).toBeInTheDocument();
    expect(screen.getByText("c2 description")).toBeInTheDocument();
  });

  it("renders evidence details for conditions", () => {
    render(<EvaluationTree node={leaf("c1", true)} />);
    expect(screen.getByText("Current value")).toBeInTheDocument();
    expect(screen.getByText("101.5")).toBeInTheDocument();
    expect(screen.getByText(/latest candle must satisfy/)).toBeInTheDocument();
  });

  it("expands nested children on click", async () => {
    const nested: EvaluationNode = {
      kind: "group",
      id: "g1",
      logic: "OR",
      satisfied: true,
      description: "AT LEAST ONE of 1 condition(s)/group(s) must hold",
      evidence: {},
      children: [leaf("deep", true)],
    };
    const root: EvaluationNode = {
      ...group,
      children: [nested],
    };
    render(<EvaluationTree node={root} />);
    // Nested group starts collapsed (depth 1)
    expect(screen.queryByText("deep description")).not.toBeInTheDocument();
    await userEvent.click(screen.getByText("AT LEAST ONE of 1 condition(s)/group(s) must hold"));
    expect(screen.getByText("deep description")).toBeInTheDocument();
  });
});

describe("ModeSwitch", () => {
  it("renders both modes and marks the active one", () => {
    render(<ModeSwitch value="forex" onChange={() => {}} />);
    expect(screen.getByRole("radio", { name: "Forex" })).toBeChecked();
    expect(screen.getByRole("radio", { name: "Binary Options" })).not.toBeChecked();
  });

  it("calls onChange with the selected mode", async () => {
    const onChange = vi.fn();
    render(<ModeSwitch value="forex" onChange={onChange} />);
    await userEvent.click(screen.getByRole("radio", { name: "Binary Options" }));
    expect(onChange).toHaveBeenCalledWith("binary_options");
  });
});
