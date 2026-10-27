import { describe, expect, it } from "vitest";
import { type Family, atCeiling, floorText, judged } from "./quality";

const family = (over: Partial<Family> = {}): Family => ({
  family: "author", cases: 6, runs: 3, failure_rate: 0, gated_context_precision: true,
  hit_at_k: 1, answer_evidence_recall: 1, ...over,
});

describe("quality helpers", () => {
  it("flips the floor sign for metrics where lower is better", () => {
    const gate = { measured: 0, allowed_drop: null, headroom: 0.133, observed_spread: 0, gated: true };
    expect(floorText({ ...gate, metric: "answer_failure_rate", floor: 0.133 })).toBe("≤ 0.133");
    expect(floorText({ ...gate, metric: "hit_at_k", floor: 0.5 })).toBe("0.5");
  });

  it("shows the spread of three runs only when there is one", () => {
    expect(judged(family({ faithfulness: 0.9537, faithfulness_range: [0.9167, 1] }), "faithfulness")).toBe("0.954 (0.92~1.00)");
    expect(judged(family({ context_precision: 1, context_precision_range: [1, 1] }), "context_precision")).toBe("1.000");
    expect(judged(family(), "answer_relevancy")).toBe("—");
  });

  it("says when these cases cannot tell the families apart", () => {
    expect(atCeiling([family(), family({ family: "citing" })])).toBe(true);
    expect(atCeiling([family(), family({ failure_rate: 0.0556 })])).toBe(false);
  });
});
