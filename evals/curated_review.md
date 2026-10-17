# 큐레이션 토픽 후보 검토 기록

`evals/cases_curated.json` 10건 · 정답 ID 30개 · 기계 검증 2026-10-13 · **사람 검토 2026-10-18 (오현탁)**

> **2026-10-18에 사람 판단이 필요했던 3건이 해결됐습니다**(검토자 오현탁). 그 3건은
> `review_status`가 `human_reviewed`이고, 나머지 7건은 **`assistant_reviewed`로 남았습니다** —
> 검토자가 기계 검증 **규칙에는 동의**했지만 논문을 한 편씩 읽은 것은 아니므로, 상태를 실제보다
> 강하게 적지 않았습니다. 그 동의 사실은 각 사례의 `review.rule_accepted_by`에 있습니다.

## 요약

| 항목 | 결과 |
|---|---|
| 정답 ID 기계 검증 통과 | **27 / 30** (질의어가 제목·초록에 나타남) |
| 판단으로 유지 | 2건 → **사람 검토 완료** |
| 사람 확인 권장 | 1건 → **사람 검토 완료 (유지)** |
| 제외(drop) | 0건 |
| `human_reviewed` | **3건** (직접 본 사례) |
| `assistant_reviewed` | 7건 (규칙 동의, 개별 열람 없음) |
| 필수 답변 문구 | **10건 전부 폐기** |

기계 검증은 질의어를 하이픈·복수형까지 정규화해 제목·초록에 나타나는지 봤습니다.
통과하지 못한 3건만 판단으로 기록했습니다.

---

## 사람 확인이 필요했던 항목 — 해결 (2026-10-18)

### `curated-topic-medical-imaging` → [`1612.07003`](https://arxiv.org/abs/1612.07003)

**Image biomarker standardisation initiative** · `cs.CV`

질의어 `medical imaging`이 제목·초록에 전혀 없고 다른 두 논문보다 주제 중심성이 낮아 사람
확인을 요청했습니다.

- [x] **유지** (검토자 오현탁, 2026-10-18)
- [ ] 제외 (`expected_ids`에서 빼기)

**유지한 이유.** 라디오믹스 영상 바이오마커 표준화는 의료영상 연구이고, 더 중요하게는
**정답 ID 30개 중 질의어가 전혀 없는 유일한 사례**입니다(나머지 29개는 질의어가 제목이나 초록에
있고, 그중 2개는 제목에서 쪼개져 있을 뿐입니다). 그래서 이 한 건이 이 세트에서 **키워드 없이
의미만으로 찾는 능력을 재는 자리**이고, 제외하면 그 측정이 사라집니다.

**대가도 적어 둡니다.** 라벨 기준이 둘이 됩니다 — 29건은 "질의어가 나타남", 1건은 "주제가
인접함". 그래서 이 케이스의 recall이 낮게 나올 때 **"검색이 나쁘다"로 읽지 말고 이 사례를 먼저
확인해야 합니다.** `review.gold_id_checks`의 evidence에 같은 경고를 적어 뒀습니다.

---

## 판단으로 유지한 항목 — 사람 검토 완료 (2026-10-18)

기계 검증은 통과하지 못했지만 주제에 맞아 유지했고, 검토자가 둘 다 확인해 **유지**로
판정했습니다. 둘 다 질의어가 제목에서 쪼개져 있을 뿐 다 들어 있습니다.

| 케이스 | arXiv ID | 제목 | 이유 |
|---|---|---|---|
| `graph-neural-network` | [`1904.07785`](https://arxiv.org/abs/1904.07785) | Graph Wavelet Neural Network | 제목이 'Graph Wavelet Neural Network'로 질의어가 연속으로 나오지 않지만 그래프 신경망 연구다. 자동 규칙의 한계이고 라벨은 맞다. |
| `retrieval-augmented-generation` | [`2305.15294`](https://arxiv.org/abs/2305.15294) | Enhancing Retrieval-Augmented Large Language Models with Iterative Retrieval-Generation Synergy | 제목이 'Retrieval-Augmented Large Language Models'와 'Retrieval-Generation Synergy'로 쪼개져 질의어가 연속으로 없지만 RAG 연구다. |

---

## 필수 답변 문구를 전부 폐기한 이유

폐기한 문구는 정답 3편 중 한 편의 초록에서 그대로 떼어 온 5~10단어 조각이다. 그 문구를 요구하면 '정답이 맞는가'가 아니라 '그 한 편의 초록을 그대로 인용했는가'를 재게 된다. cases.json의 title-03·title-10에서 정답 ID를 맞혔는데도 답변이 짧아져 문구 검사에 탈락한 사례가 실제로 측정됐고, 재실행하면 결과가 뒤집히기도 했다. 빈 목록으로 두면 answer_correct가 None이 되어 집계에서 빠지고, 채점은 원천으로 뒷받침되는 근거 ID(answer_evidence_precision/recall)에 의존한다.

폐기한 문구는 `review.retired_answer_phrase`에 보존돼 있습니다. 다음과 같은 형태였습니다.

| 케이스 | 폐기한 문구 | 출처 논문 |
|---|---|---|
| `graph-neural-network` | `achieved promising results in graph analysis tasks` | 2011.11288 |
| `retrieval-augmented-generation` | `conditioning generation on external evidence retrieved at inference time` | 2506.00054 |
| `diffusion-model` | `modelling categorical data with diffusion models` | 2211.15089 |
| `reinforcement-learning` | `deep RL methods that can adapt rapidly to new tasks` | 1611.05763 |
| `multimodal` | `process and relate information from multiple modalities` | 1705.09406 |
| `knowledge-graph` | `creation, enrichment, quality assessment, refinement, and publication` | 2003.02320 |
| `code-generation` | `multi-step paradigm for program synthesis` | 2203.13474 |
| `medical-imaging` | `research in image acquisition, algorithms, data standardization` | 2103.01938 |
| `federated-learning` | `training statistical models over remote devices` | 1908.07873 |
| `AI-agents` | `modules for perception, planning, and tool use` | 2503.12687 |

정답이 3편인데 문구는 그중 **한 편**의 초록 조각입니다. 다른 두 편을 근거로 올바르게
답해도 탈락합니다.

`expected_answer_contains`를 빈 목록으로 두면 `answer_correct`가 `None`이 되어 집계에서
빠지고, 채점은 원천으로 뒷받침되는 근거 ID에 의존합니다. 문구 기준이 필요하면 특정 논문의
표현이 아니라 주제 자체를 담은 문구로 다시 써야 합니다.

---

## 승격 범위와 그 이유

**세 건만 `human_reviewed`로 올렸습니다.**

| 사례 | 상태 | 근거 |
|---|---|---|
| `curated-topic-medical-imaging` | `human_reviewed` | 플래그 항목을 직접 보고 유지 판정 |
| `curated-topic-graph-neural-network` | `human_reviewed` | 판단 유지 항목을 직접 확인 |
| `curated-topic-retrieval-augmented-generation` | `human_reviewed` | 판단 유지 항목을 직접 확인 |
| 나머지 7건 | `assistant_reviewed` | 질의어 일치 규칙으로 확인. 검토자는 **규칙에 동의**했고 논문을 개별 열람하지 않았습니다 |

10건 전부를 올리지 않은 이유는 하나입니다 — **실제로 본 것은 3건인데 10건에 사람 검토 도장을
찍으면 기록이 실제보다 강해집니다.** 규칙에 동의한 사실은 각 사례의 `review.rule_accepted_by`에
따로 적혀 있고, `tests/test_eval_dataset.py`가 그 구별을 고정합니다.

**점수는 바뀌지 않았습니다.** 정답 ID를 하나도 바꾸지 않았으므로(유지 판정) 이전 보고서와
비교 가능합니다.

```bash
uv run python -m evals.run --mode compare --cases evals/cases_curated.json \
  --limit 10 --top-k 5 --output reports/rag_curated_compare.json
```

