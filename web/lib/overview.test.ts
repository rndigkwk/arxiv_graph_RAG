import { describe, expect, it } from "vitest";
import { placeCommunities, sqrtScale } from "./overview";

const row = (label: string, papers: number) => ({ label, papers, primary_category: "cs.AI", secondary_category: null, inside: 0 });

describe("overview helpers", () => {
  it("grows by the square root so a big community does not swallow the rest", () => {
    expect(sqrtScale(100, 100, 18, 46)).toBe(46);
    expect(sqrtScale(25, 100, 18, 46)).toBe(32);
    expect(sqrtScale(5, 0, 18, 46)).toBe(18);
  });

  it("puts the largest at the top and keeps every circle inside the box", () => {
    const placed = placeCommunities([row("C1", 3874), row("C2", 2725), row("C3", 1827)], 520, 360);
    expect(placed[0].x).toBeCloseTo(260);
    expect(placed[0].y).toBeLessThan(placed[1].y);
    for (const p of placed) {
      expect(p.x - p.r).toBeGreaterThanOrEqual(0);
      expect(p.y - p.r).toBeGreaterThanOrEqual(0);
      expect(p.x + p.r).toBeLessThanOrEqual(520);
      expect(p.y + p.r).toBeLessThanOrEqual(360);
    }
  });
});
