import { describe, expect, it } from "vitest";
import { categoryLabel, categoryName } from "./categories";

describe("category names", () => {
  it("names the codes people see and leaves unknown ones as codes", () => {
    expect(categoryLabel("cs.AI")).toBe("인공지능 (cs.AI)");
    expect(categoryName("cs.CY")).toBe("컴퓨터와 사회");
    expect(categoryLabel("astro-ph.GA")).toBe("astro-ph.GA");
  });

  it("covers every category the explore screen offers today", () => {
    // 2026-11-06 /api/stats의 상위 10개 분류.
    for (const code of ["cs.AI", "cs.CL", "cs.LG", "cs.CV", "cs.RO", "cs.SE", "cs.IR", "cs.CR", "cs.HC", "cs.CY"])
      expect(categoryName(code)).not.toBe(code);
  });
});
