import { describe, expect, it } from "vitest";
import type { Answer } from "./api";
import { citedEvidence, graphIds, historyFor, routeLine, usedPath } from "./answer";

const answer = (over: Partial<Answer> = {}): Answer => ({
  question: "q",
  answer: "a",
  evidence_ids: ["2407.00215", "2509.15541"],
  tool_calls: [{ id: "1", name: "search_graph", args: {} }],
  cyphers: ["MATCH ..."],
  rows: [{ "p.arxiv_id": "2407.00215" }],
  papers: [],
  related: [{ arxiv_id: "2401.05566" }, { arxiv_id: "2407.00215" }],
  ...over,
});

describe("answer helpers", () => {
  it("counts only evidence that the search actually returned", () => {
    expect(citedEvidence(answer())).toEqual({ found: 1, total: 2 });
    expect(citedEvidence(answer({ papers: [{ arxiv_id: "2509.15541" }] }))).toEqual({ found: 2, total: 2 });
  });

  it("names the path by the tools that ran", () => {
    expect(usedPath(answer())).toBe("그래프 경로");
    expect(usedPath(answer({ tool_calls: [{ id: "1", name: "search_papers", args: {} }] }))).toBe("넓은 경로");
  });

  it("draws evidence first and each paper once", () => {
    expect(graphIds(answer())).toEqual(["2407.00215", "2509.15541", "2401.05566"]);
  });

  it("sends at most six earlier turns, newest kept", () => {
    const turns = Array.from({ length: 8 }, (_, i) => ({ question: `q${i}`, answer: "a" }));
    expect(historyFor(turns).map((t) => t.question)).toEqual(["q2", "q3", "q4", "q5", "q6", "q7"]);
  });

  it("explains the route with its cues", () => {
    expect(routeLine({ name: "graph", cues: ["citation"], vetoes: [] })).toContain("단서 citation");
  });
});
