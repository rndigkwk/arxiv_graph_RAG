# ADR-093: 평가 질문을 바꿔 말한 셋을 따로 둔다 — 정답은 그대로, 문형만 바꾼다

- **상태:** 채택
- **날짜:** 2026-10-28

## 맥락

회귀 평가 30문항(`evals/cases_graph.json`)은 가족마다 한 문형입니다. 저자 질문은 모두 "Which papers in this collection
list an author named X?"입니다. 그래서 **문형에 걸린 결함을 회귀 평가가 볼 수 없습니다.** 한 번 놓쳤습니다 - "최근"을
담은 질문이 0건이어서 24~44초 걸리던 문제를 VM에서 따로 묻고서야 알았습니다([ADR-090](ADR-090-recent-is-a-date-filtered-search.md)).
교안 Day 69의 패러프레이즈 증강을 이 빈자리에 씁니다([lecture-review](../lecture-review.md)).

이 평가의 정답은 Cypher에서 나옵니다(`label_method: snapshot_cypher`). **뜻이 같으면 정답도 같으므로** 바꿔 말한 질문에
정답을 다시 셀 필요가 없습니다. 남는 일은 뜻이 같다는 것을 보증하는 것입니다.

## 결정

**30문항마다 영어 하나, 한국어 하나로 바꿔 말한 60문항을 `evals/cases_graph_paraphrase.json`에 둡니다**
(`evals/paraphrase_questions.py`).

- **문형은 손으로 썼습니다.** 가족마다 두 개입니다. 모델로 만들면 모델이 잘 알아듣는 문형만 나올 수 있습니다.
- **뜻이 같은지는 기계적으로 봅니다**(`check`). 정답을 정하는 값 - 저자 이름, 대상 논문 제목, 분류, 개수 - 이 바꾼 질문에
  글자 그대로 있어야 하고, 정답 ID가 원문과 같아야 합니다. 한국어 질문도 제목과 이름은 영어 그대로 둡니다(사용자가
  실제로 그렇게 묻습니다).
- `topcited`는 "주 분류"를 빼지 않았습니다. 빼면 보조 분류로 그 분야인 논문까지 들어가 정답이 달라집니다.
- `tests/test_paraphrase_questions.py`가 커밋된 파일과 템플릿이 같은지, 값이 빠지면 잡는지 봅니다.

~~**게이트에는 넣지 않습니다.** 게이트의 기준선은 원문 30문항으로 만들었고, 문항이 늘면 하한과 안정 문항 규칙
([ADR-055](ADR-055-the-stable-case-rule-is-not-resolved.md))을 다시 정해야 합니다. 지금은 따로 돌리는 측정입니다.~~
**뒤집힘(2026-10-29, 아래 "게이트에 넣음"):** 하한을 다시 정하지 않는 방식으로 넣었습니다. 집계는 비교하지 않고
사례별 만점만 봅니다.

## 결과 (2026-10-28, 로컬, `reports/paraphrase_eval.json`)

`python -m evals.run --mode compare --cases evals/cases_graph_paraphrase.json --limit 60`, main(d5f122d)의 코드입니다.

| 가족 | 영어 정밀도·재현율 | 영어 p50 | 한국어 정밀도·재현율 | 한국어 p50 |
|---|---:|---:|---:|---:|
| author | 1.0 · 1.0 | 6.11초 | 1.0 · 1.0 | 8.12초 |
| citing | 1.0 · 1.0 | 6.04초 | 1.0 · 1.0 | 8.01초 |
| coauthor | 1.0 · 1.0 | 6.48초 | 1.0 · 1.0 | 8.78초 |
| reference | 1.0 · 1.0 | 6.06초 | 1.0 · 1.0 | 7.83초 |
| topcited | 1.0 · 1.0 | 5.81초 | 1.0 · 1.0 | 7.35초 |

**60문항 모두 원문과 같은 점수였습니다.** 실패 0, 재시도 0, 평균 모델 턴 2.05입니다. 이 다섯 가족에는 문형에 걸린 결함이
보이지 않았습니다. 한국어 질문은 2초쯤 느리고, 그 대부분이 앞단 번역입니다(번역 p50 1.72초, [ADR-087](ADR-087-translate-before-the-agent.md)).

같은 셋을 처음 `--mode agent`로 돌렸을 때 **한 문항이 보류**했습니다(판정이 `incomplete_content_verdict`로 두 번
돌려보냄). `agent` 모드는 보류를 예외로 올려 실행이 거기서 멈췄고, 어느 문항인지는 기록이 남지 않았습니다. 다시 돌린
`compare` 모드에서는 60문항 모두 통과했습니다 - 한 번의 흔들림으로 봅니다. 이 셋은 `compare` 모드로 돌립니다.

## 게이트에 넣음 (2026-10-29)

위에서 넣지 않은 이유는 "하한과 안정 문항 규칙을 다시 정해야 한다"였습니다. 둘 다 다시 정하지 않고 넣는 길이 있습니다.

- **`paraphrase` 분할**을 만들었습니다(`baselines/eval_baseline.json`의 `splits.paraphrase`, 60건). smoke처럼 기준선과
  모수가 다르므로 **집계 규칙(`floor`·`drop`)은 적용하지 않습니다**(`gate.compares_aggregates`).
- 대신 **60건 모두 만점을 지켜야 합니다**(`split_stable_cases.paraphrase`). `stable_cases`는 기준선을 다시 쓸 때 full
  실행에서 새로 계산되므로 거기 섞지 않고 분할별 칸에 따로 둡니다. 재작성이 그대로 들고 옵니다
  (`gate.stable_cases`, `tests/test_paraphrase_gate.py`).
- 에이전트가 예외로 멈춘 사례는 위반이 아니라 정보입니다. full의 안정 사례와 같은 규칙입니다.
- **PR마다 돌리지 않습니다.** 주간 크론을 하나 더 두었습니다(한국 시간 월요일 오전 8시, full 한 시간 뒤). 수동 실행에서
  `split=paraphrase`로도 돌립니다. 두 크론이 서로를 취소하지 않게 `concurrency` 묶음에 분할을 넣었습니다.
- 장부(`reports/case_ledger.jsonl`)에는 데이터셋이 다른 묶음으로 쌓입니다. 몇 주 쌓이면 바꿔 말한 질문의 흔들림을
  ADR-055와 같은 방식으로 셀 수 있습니다.

**첫 실행:** 브랜치에서 `split=paraphrase`를 손으로 돌려 확인합니다(결과는 아래에 덧붙입니다).

## 민감도

- **"최근" 같은 문형은 여기 없습니다.** 바꿔 말하기는 뜻을 지키므로, 뜻이 다른 질문("최근", 기간, 혼합 조건)을 만들지
  않습니다. ADR-090의 결함은 이 셋으로도 잡히지 않았을 것입니다 - 그런 질문은 정답을 새로 세어야 하는 다른 셋입니다.
- **한 번 잰 결과입니다.** 60문항 모두 1.0이므로 흔들림을 말할 수 없습니다. 게이트가 60건 모두 만점을 요구하므로, 생성이 흔들리면 회귀가 없는 주에도 빨갛게 뜰 수 있습니다. 그때는 그 사례를 `split_stable_cases`에서 빼고 이유를 적습니다(규칙을 끄지 않습니다). 지시문이나 번역을 고칠 때 이 셋을 함께
  돌려, 원문은 그대로인데 이 셋만 떨어지는지를 봅니다.
- 문형이 손으로 쓴 두 개뿐입니다. 사용자가 실제로 묻는 말(VM 로그)이 쌓이면 거기서 문형을 더합니다.

## 참고

- [`evals/paraphrase_questions.py`](../../evals/paraphrase_questions.py), `evals/cases_graph_paraphrase.json`,
  [`tests/test_paraphrase_questions.py`](../../tests/test_paraphrase_questions.py)
- 보고서: `reports/paraphrase_eval.json`
- ADR-055(안정 문항 규칙), ADR-087(앞단 번역), ADR-090("최근"), 교안 Day 69
