"""인용망을 짓는 데 걸리는 시간을 잽니다 — **예열이 옮기는 값**이 얼마인지.

    uv run python scripts/measure_citation_graph_warmup.py
    uv run python scripts/measure_citation_graph_warmup.py --repeats 3
    uv run python scripts/measure_citation_graph_warmup.py \
        --output reports/citation_graph_warmup.json

[ADR-066](../docs/adr/ADR-066-measure-inside-generate.md)이 `generate` 안쪽을 가르자
배포 앱의 첫 질문에서 `rank_related_papers`가 26.49초, 바로 다음 질문에서 0.83초로 나왔습니다.
그 차이가 전부 `chatbot.citation_graph()`입니다. 이 스크립트는 **그 한 덩어리만** 재서,
예열이 어느 쪽으로 얼마를 옮기는지 수로 말할 수 있게 합니다.

**모델을 부르지 않습니다.** Neo4j를 두 번 읽고 행렬을 만드는 것이 전부라 비용은 0원입니다.

**차가운 시작이 아니라 다시 짓기를 잽니다.** 반복할 때마다 `graph_cache`를 비우므로
파이썬·드라이버·numpy는 이미 더운 상태입니다. 재려는 것이 그 아래의 **읽기와 행렬 만들기**
이므로 그쪽이 맞고, 진짜 첫 실행은 여기에 임포트 시간이 더 붙습니다.

메모리는 여기서 재지 않습니다 — [`measure_app_memory.py`](measure_app_memory.py)가
`citation_graph_loaded` 단계로 이미 재고 있어, 같은 값을 두 군데서 재면 갈라집니다.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from time import perf_counter
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def measure(repeats: int) -> dict[str, Any]:
    from src import chatbot

    cold: list[float] = []
    warm: list[float] = []
    for _ in range(max(1, repeats)):
        chatbot.graph_cache.clear()
        start = perf_counter()
        graph = chatbot.citation_graph()
        cold.append(perf_counter() - start)
        start = perf_counter()
        chatbot.citation_graph()
        warm.append(perf_counter() - start)

    return {
        "repeats": len(cold),
        "papers": len(graph["ids"]),
        "edges": int(len(graph["rows"])),
        "build_seconds": {
            "p50": round(statistics.median(cold), 2),
            "min": round(min(cold), 2),
            "max": round(max(cold), 2),
        },
        # 더운 조회는 딕셔너리 반환이라 0에 가깝습니다. 그것이 요점입니다 - 옮길 값이
        # **한 번만** 있고 그 뒤로는 없다는 뜻이기 때문입니다.
        "cached_seconds": {"max": round(max(warm), 4)},
        "note": (
            "예열은 이 시간을 없애지 않고 옮깁니다. 짓는 동안 들어온 질문은 graph_lock에서 "
            "기다리므로, 빨라지는 것이 아니라 홈 화면을 읽는 시간과 겹쳐집니다."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=3, help="다시 짓기를 몇 번 잴지")
    parser.add_argument("--output", type=Path, help="보고서를 쓸 경로")
    args = parser.parse_args()

    report = measure(args.repeats)
    build = report["build_seconds"]
    print(f'논문 {report["papers"]:,}편 · 간선 {report["edges"]:,}개')
    print(f'다시 짓기 {report["repeats"]}회: p50 {build["p50"]}초 '
          f'(최소 {build["min"]} · 최대 {build["max"]})')
    print(f'캐시된 조회: 최대 {report["cached_seconds"]["max"]}초')
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"기록: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
