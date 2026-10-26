# ADR-077: 평가 하네스도 앱처럼 인용망을 데운다 — 동시에 돈 도구는 한 번만 센다

- **상태:** 채택
- **날짜:** 2026-10-26

## 맥락

2026-10-26 유료 실행 30문항(EXPERIMENTS 11-52)에서 두 가지가 앱과 다르게 재졌습니다.

1. **첫 관련 논문 호출이 26.1초였습니다**(`author-02`). 나머지 24번은 p50 0.68초입니다. 앱은
   부팅 때 배경에서 인용망을 올리므로([ADR-067](ADR-067-warm-the-citation-graph-at-boot.md))
   사용자는 그 값을 겪지 않습니다. 하네스만 그 값을 한 문항에 몰아 줬고, 요약 스크립트가 그 한
   번을 손으로 떼어 내고 있었습니다.
2. **`citing-02`의 나머지가 -0.194초였습니다.** 30문항 중 한 모델 턴이 도구 둘
   (`search_graph`·`rank_related_papers`)을 부른 문항이 이것 하나였고, 음수인 문항도 이것
   하나였습니다. LangGraph가 둘을 동시에 돌렸고, 계측은 호출마다의 초를 **더해서** 겹친 구간을
   두 번 셌습니다.

## 결정

- `evals/run.py`의 `warm_citation_graph`가 문항 고리 **전에** `chatbot.citation_graph()`를
  부릅니다. 걸린 초는 `metadata.citation_graph_warmup_seconds`에 남깁니다. 검색만 재는
  `retrieval` 모드는 그 도구를 부르지 않으므로 데우지 않습니다(`None`).
- `src/tool_timing.py`의 도구 몫(`tools_seconds`)은 **구간의 합집합**입니다. 호출마다의 합은
  `tools_seconds_summed`로 따로 두고, 호출마다의 초(누가 길었는지)는 그대로 둡니다.
- `scripts/measure_inside_generate.py`는 예열한 보고서에서 차가운 호출을 떼어 내지 않습니다. 한
  턴에 도구가 둘 이상인 문항은 `parallel_tool_cases`로 이름을 붙입니다 - **저장된 보고서는 구간이
  없어 다시 셀 수 없기** 때문입니다. 2026-10-26 보고서에서는 `citing-02` 하나입니다.

## 민감도

- **예열 전후의 지연은 바로 비교되지 않습니다.** 이 변경 뒤의 실행은 첫 관련 논문 문항이 약 25초
  짧아집니다. `metadata.citation_graph_warmup_seconds`가 없는 보고서는 이 변경 전의 것입니다.
- **예열이 실패하면 평가도 실패합니다.** 앱은 예열이 죽어도 질문을 받지만(첫 질문이 느릴 뿐),
  하네스에서 Aura를 못 읽으면 어차피 문항도 못 돕니다. 앞에서 멈추는 쪽이 싸다고 봤습니다.
- **합집합은 다른 스레드의 시계도 같은 `perf_counter`라고 가정합니다.** 한 프로세스 안이므로
  맞습니다.
- 겹침 보정이 바꾸는 집계는 작습니다. 2026-10-26 실행의 도구 몫 0.202에서 최대 0.194초/409.1초
  (0.0005)입니다. 고친 이유는 크기가 아니라 `adds_up`이 틀린 이유를 가리켰기 때문입니다.

## 참고

- `tests/test_eval_warmup.py`, `tests/test_tool_timing.py`(겹친 구간), `tests/test_measure_inside_generate.py`
- [ADR-066](ADR-066-measure-inside-generate.md) — 도구 호출마다 재는 장치
