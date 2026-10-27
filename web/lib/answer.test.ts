import { describe, expect, it } from "vitest";
import type { Answer } from "./api";
import { GENERATE_CONTAINS, citedEvidence, generateSplit, graphIds, historyFor, routeLine, stageRows, usedPath } from "./answer";

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

  it("lists every stage slowest first, keeps zero-second ones, and ends with the total", () => {
    const rows = stageRows(
      answer({
        graph_trace: {
          seconds: { route: 0, generate: 9.5, check_format: 0.2 },
          visits: { route: 1, generate: 2, check_format: 2 },
          attempts: 2,
          total_seconds: 9.7,
        },
      }),
    );
    expect(rows.map((r) => r.node)).toEqual(["답변 생성", "형식·인용 검사", "경로 판정", "합계"]);
    expect(rows.at(-1)).toMatchObject({ seconds: 9.7, visits: 5 });
    expect(rows[0].note).toBe(GENERATE_CONTAINS);
    expect(stageRows(answer())).toEqual([]);
  });

  it("splits generation only when the parts add up", () => {
    const inside = { adds_up: true, model_seconds: 6, model_turns: 3, tools_seconds: 2.5, tool_calls: 2, unattributed_seconds: 0, other_seconds: 1 };
    expect(generateSplit(answer({ generate_trace: inside }))).toBe("모델 6.0초 (3턴) · 도구 2.5초 (2회) · 그 외 1.0초");
    expect(generateSplit(answer({ generate_trace: { ...inside, adds_up: false } }))).toBe("");
  });
});
