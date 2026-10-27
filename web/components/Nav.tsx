"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

// 시안의 네 화면입니다(docs/MIGRATION_PLAN.md 4단계). 휴대폰은 같은 화면의 좁은 배치입니다.
const PAGES = [
  { href: "/", label: "질문" },
  { href: "/papers", label: "논문" },
  { href: "/explore", label: "탐색" },
  { href: "/quality", label: "품질" },
];

const current = (path: string, href: string) => (href === "/" ? path === "/" : path.startsWith(href));

export function Nav() {
  const path = usePathname();
  return (
    <nav aria-label="주 메뉴" className="nav">
      {PAGES.map((p) => (
        <Link key={p.href} href={p.href} aria-current={current(path, p.href) ? "page" : undefined}>
          {p.label}
        </Link>
      ))}
    </nav>
  );
}
