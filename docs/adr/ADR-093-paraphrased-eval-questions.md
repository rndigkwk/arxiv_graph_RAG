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

**게이트에는 넣지 않습니다.** 게이트의 기준선은 원문 30문항으로 만들었고, 문항이 늘면 하한과 안정 문항 규칙
([ADR-055](ADR-055-the-stable-case-rule-is-not-resolved.md))을 다시 정해야 합니다. 지금은 따로 돌리는 측정입니다.

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

## 민감도

- **"최근" 같은 문형은 여기 없습니다.** 바꿔 말하기는 뜻을 지키므로, 뜻이 다른 질문("최근", 기간, 혼합 조건)을 만들지
  않습니다. ADR-090의 결함은 이 셋으로도 잡히지 않았을 것입니다 - 그런 질문은 정답을 새로 세어야 하는 다른 셋입니다.
- **한 번 잰 결과입니다.** 60문항 모두 1.0이므로 흔들림을 말할 수 없습니다. 지시문이나 번역을 고칠 때 이 셋을 함께
  돌려, 원문은 그대로인데 이 셋만 떨어지는지를 봅니다.
- 문형이 손으로 쓴 두 개뿐입니다. 사용자가 실제로 묻는 말(VM 로그)이 쌓이면 거기서 문형을 더합니다.

## 참고

- [`evals/paraphrase_questions.py`](../../evals/paraphrase_questions.py), `evals/cases_graph_paraphrase.json`,
  [`tests/test_paraphrase_questions.py`](../../tests/test_paraphrase_questions.py)
- 보고서: `reports/paraphrase_eval.json`
- ADR-055(안정 문항 규칙), ADR-087(앞단 번역), ADR-090("최근"), 교안 Day 69
