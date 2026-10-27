import { describe, expect, it } from "vitest";
import { ARXIV_ID, MAX_SAVED, askHref, bibtexFile, parseSaved, questionsFor, toggleSaved } from "./paper";

const paper = (id: string) => ({ arxiv_id: id, title: `T ${id}`, bibtex: `@misc{${id},}` });

describe("paper helpers", () => {
  it("toggles a paper in and out and stops at the limit", () => {
    const one = toggleSaved([], paper("2210.03629"));
    expect(one.map((s) => s.arxiv_id)).toEqual(["2210.03629"]);
    expect(toggleSaved(one, paper("2210.03629"))).toEqual([]);
    const full = Array.from({ length: MAX_SAVED }, (_, i) => paper(`2401.${String(i).padStart(5, "0")}`));
    expect(toggleSaved(full, paper("2210.03629"))).toHaveLength(MAX_SAVED);
  });

  it("reads a broken store as empty", () => {
    expect(parseSaved(null)).toEqual([]);
    expect(parseSaved("{not json")).toEqual([]);
    expect(parseSaved('[{"arxiv_id":"a"},{"arxiv_id":"b","title":"B","bibtex":"@x"}]')).toEqual([
      { arxiv_id: "b", title: "B", bibtex: "@x" },
    ]);
  });

  it("writes one file with the author caveat on top", () => {
    const file = bibtexFile([paper("a"), paper("b")]);
    expect(file.startsWith("% ")).toBe(true);
    expect(file).toContain("@misc{a,}\n\n@misc{b,}");
  });

  it("fills the question box without sending", () => {
    const qs = questionsFor({ title: "ReAct: Synergizing Reasoning", primary_category: "cs.CL" });
    expect(qs[0].label).toBe("ReAct를 인용한 cs.CL 논문");
    expect(askHref(qs[0].question)).toBe("/?q=Which%20cs.CL%20papers%20cite%20ReAct%3A%20Synergizing%20Reasoning%3F");
  });

  it("knows an arXiv id when it sees one", () => {
    expect(ARXIV_ID.test("2210.03629")).toBe(true);
    expect(ARXIV_ID.test("2210.03629v3")).toBe(true);
    expect(ARXIV_ID.test("ReAct")).toBe(false);
  });
});
