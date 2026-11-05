"""주간 수집의 참고문헌 간선과 그래프 밖 논문을 정합니다(ADR-099, ADR-104).

S2가 돌려준 참고문헌(`pipeline/s2.py`)을 셋으로 나눕니다.

- **그래프 안 논문을 가리키는 것** - 바로 간선이 됩니다. 이번 배치의 새 논문끼리도 여기입니다.
- **그래프 밖 논문을 가리키는 것** - 대기열에 "누가 인용했는지"를 쌓습니다. 인용한 새 논문이
  **누적 3편**이 되는 주에 그 논문을 노드로 들이고, 쌓인 간선을 함께 붙입니다(ADR-099).
  3회를 고른 이유는 그 측정에 있습니다 - 2회는 들인 논문의 16%만, 3회는 50%가 그 뒤 주에 다시
  인용됐습니다.
- **참고문헌이 아직 없는 새 논문** - S2는 새 논문의 참고문헌을 며칠~몇 주 늦게 채웁니다. 다음
  실행에서 다시 묻습니다. ADR-099에서 이어지지 않는 새 논문 30%의 대부분이 이 경우였습니다.

모두 순수 함수입니다. 그래프·네트워크·파일을 만지지 않습니다 - 읽고 쓰는 것은 `ingest.main`입니다.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

STATE_PATH = Path("pipeline/state/ingest_references.json")
# ADR-099의 결정입니다. 바꾸려면 그 측정(`scripts/measure_ingest_criteria.py`)부터 다시 돌립니다.
OUTSIDE_MIN_CITERS = 3
# 참고문헌이 비어 있던 새 논문을 몇 번 더 물을지입니다. 주 1회 실행이면 3주입니다.
RETRY_RUNS = 3


def read_state(path: Path = STATE_PATH) -> dict[str, Any]:
    if not path.exists():
        return {"waiting": {}, "retry": {}}
    state = json.loads(path.read_text(encoding="utf-8"))
    state.setdefault("waiting", {})
    state.setdefault("retry", {})
    return state


def write_state(state: dict[str, Any], path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True) + "\n",
                    encoding="utf-8")


def candidate_ids(references: dict[str, list[str]], waiting: dict[str, list[str]]) -> set[str]:
    """그래프에 있는지 물어볼 ID들: 이번 참고문헌이 가리키는 것과 대기열에 있는 것."""
    return {cited for ids in references.values() for cited in ids} | set(waiting)


def count_citations(
    references: dict[str, list[str]],
    waiting: dict[str, list[str]],
    *,
    in_graph: set[str],
) -> tuple[list[dict[str, str]], dict[str, list[str]]]:
    """간선과 새 대기열을 돌려줍니다. 받은 대기열은 바꾸지 않습니다.

    `in_graph`에는 **이번 배치의 새 논문도** 들어 있어야 합니다. 새 논문끼리의 인용도 간선입니다.
    대기열에 있던 논문이 그사이 그래프에 들어왔으면(수집 창에 잡혔거나) 쌓인 인용을 간선으로
    바꾸고 대기열에서 뺍니다.
    """
    pending = {cited: list(citers) for cited, citers in waiting.items()}
    edges: list[dict[str, str]] = []
    for citing, cited_ids in references.items():
        for cited in dict.fromkeys(cited_ids):
            if not cited or cited == citing:
                continue
            if cited in in_graph:
                edges.append({"citing": citing, "cited": cited})
            elif citing not in pending.setdefault(cited, []):
                pending[cited].append(citing)
    for cited in [cited for cited in pending if cited in in_graph]:
        edges.extend({"citing": citing, "cited": cited} for citing in pending.pop(cited))
    return _unique(edges), pending


def ready(waiting: dict[str, list[str]], minimum: int = OUTSIDE_MIN_CITERS) -> list[str]:
    """누적 인용이 문턱을 넘은 밖 논문입니다. 많이 인용된 순, 같으면 ID 순입니다."""
    return sorted((cited for cited, citers in waiting.items() if len(set(citers)) >= minimum),
                  key=lambda cited: (-len(set(waiting[cited])), cited))


def admit(waiting: dict[str, list[str]], admitted: Iterable[str]
          ) -> tuple[list[dict[str, str]], dict[str, list[str]]]:
    """들인 밖 논문의 쌓인 인용을 간선으로 바꾸고 대기열에서 뺍니다.

    메타데이터를 받지 못한 논문은 `admitted`에 넣지 않습니다 - 대기열에 남아 다음 주에 다시
    들일 후보가 됩니다.
    """
    pending = {cited: list(citers) for cited, citers in waiting.items()}
    edges = [{"citing": citing, "cited": cited}
             for cited in dict.fromkeys(admitted) if cited in pending
             for citing in pending.pop(cited)]
    return _unique(edges), pending


def next_retry(retry: dict[str, int], *, asked: Iterable[str], resolved: Iterable[str],
               runs: int = RETRY_RUNS) -> tuple[dict[str, int], list[str]]:
    """다시 물을 목록을 갱신합니다. 두 번째 값은 횟수를 다 써서 내려놓은 논문입니다.

    `asked`는 이번에 S2에 물었는데 참고문헌을 받지 못한 논문, `resolved`는 받은 논문입니다.
    값은 지금까지 못 받은 횟수입니다. 첫 실패가 1이므로, `runs`번 더 묻고 내려놓습니다.
    """
    done = set(resolved)
    updated = {arxiv_id: count for arxiv_id, count in retry.items() if arxiv_id not in done}
    dropped: list[str] = []
    for arxiv_id in dict.fromkeys(asked):
        if arxiv_id in done:
            continue
        updated[arxiv_id] = updated.get(arxiv_id, 0) + 1
        if updated[arxiv_id] > runs:
            dropped.append(arxiv_id)
            del updated[arxiv_id]
    return updated, dropped


def _unique(edges: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    kept = []
    for edge in edges:
        key = (edge["citing"], edge["cited"])
        if key not in seen:
            seen.add(key)
            kept.append(edge)
    return kept
