# 큐레이션 토픽 후보 검토 기록

`evals/cases_curated.json` 10건 · 정답 ID 30개 · 검토일 2026-10-13

> **이 검토는 사람 검토가 아닙니다.** `review_status`는 `assistant_reviewed`이며,
> 그 자체로 이 케이스의 점수를 검증된 품질 지표로 제시할 근거가 되지 않습니다.
> 아래 **사람 확인이 필요한 항목**만 보시면 승격 여부를 판단할 수 있습니다.

## 요약

| 항목 | 결과 |
|---|---|
| 정답 ID 기계 검증 통과 | **27 / 30** (질의어가 제목·초록에 나타남) |
| 판단으로 유지 | 2건 |
| 사람 확인 권장 | **1건** |
| 제외(drop) | 0건 |
| 필수 답변 문구 | **10건 전부 폐기** |

기계 검증은 질의어를 하이픈·복수형까지 정규화해 제목·초록에 나타나는지 봤습니다.
통과하지 못한 3건만 판단으로 기록했습니다.

---

## 사람 확인이 필요한 항목

### `curated-topic-medical-imaging` → [`1612.07003`](https://arxiv.org/abs/1612.07003)

**Image biomarker standardisation initiative** · `cs.CV`

'Image biomarker standardisation initiative'. 라디오믹스 영상 바이오마커 표준화로 의료영상에 인접하지만 질의어가 제목·초록에 전혀 없고 다른 두 논문보다 주제 중심성이 낮다. 사람 확인 권장.

- [ ] 유지 (주제에 맞다)
- [ ] 제외 (`expected_ids`에서 빼기)

---

## 판단으로 유지한 항목 (참고)

기계 검증은 통과하지 못했지만 주제에 맞아 유지했습니다. 이의가 있으면 함께 보세요.

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

## 승격 방법

위 확인을 마치면 해당 케이스의 `review_status`를 `human_reviewed`로 바꾸고
`review.reviewed_by`를 본인으로 적으세요. 테스트가 그 둘의 일관성을 확인합니다.

```bash
uv run python -m evals.run --mode compare --cases evals/cases_curated.json \
  --limit 10 --top-k 5 --output reports/rag_curated_compare.json
```

