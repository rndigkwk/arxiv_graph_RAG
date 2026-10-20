# ADR-046: BGE-M3는 바를 넘지 못했다 — 5.2배 큰 모델이 작은 형제보다 나쁩니다

- **상태:** 기각
- **날짜:** 2026-10-20

## 맥락

[ADR-039](ADR-039-measure-embeddings-where-the-signal-is-only-the-embedding.md)가 **재기 전에**
어디서 재고 무엇을 넘어야 하는지 정했습니다.

> 넘어야 하는 바는 분명합니다 — 같은 집단에서 `text-embedding-3-large`를 **구간이 0을
> 제외하도록** 이겨야 합니다.

그 집단은 미래 라벨 과제의 **간선 0개 시드 1,112개**입니다. 거기서는 공동인용도 PPR도 0점이라
**임베딩이 유일한 신호**이고, 섞이는 것이 없습니다.

Colab T4에서 44분, 배치 16이 한 번도 줄지 않았고(OOM 없음), `max_seq_length` 512,
37,854 × 1024 float32, 노름 중앙값 1.0000. **인코딩은 깨끗했습니다.**

## 측정 — 이기지 못한 것이 아니라 졌습니다

간선 0개 집단(1,112 시드), `text-embedding-3-large` 대비:

| 지표 | 기준 | BGE-M3 | 차 | 구간 | 0 제외 |
|---|---:|---:|---:|---|---|
| recall@5 | <!--n:reports/embedding_slice_bge_m3.json#by_degree_slice.no_citation_edges.recall_at_k.baseline-->0.1227<!--/n--> | <!--n:reports/embedding_slice_bge_m3.json#by_degree_slice.no_citation_edges.recall_at_k.candidate-->0.0893<!--/n--> | <!--n:reports/embedding_slice_bge_m3.json#by_degree_slice.no_citation_edges.recall_at_k.mean-->-0.0334<!--/n--> | [−0.0398, −0.0270] | **예** |
| hit@5 | <!--n:reports/embedding_slice_bge_m3.json#by_degree_slice.no_citation_edges.hit_at_k.baseline-->0.4739<!--/n--> | <!--n:reports/embedding_slice_bge_m3.json#by_degree_slice.no_citation_edges.hit_at_k.candidate-->0.3786<!--/n--> | <!--n:reports/embedding_slice_bge_m3.json#by_degree_slice.no_citation_edges.hit_at_k.mean-->-0.0953<!--/n--> | [−0.1169, −0.0728] | **예** |
| MRR | <!--n:reports/embedding_slice_bge_m3.json#by_degree_slice.no_citation_edges.reciprocal_rank.baseline-->0.3457<!--/n--> | <!--n:reports/embedding_slice_bge_m3.json#by_degree_slice.no_citation_edges.reciprocal_rank.candidate-->0.2766<!--/n--> | <!--n:reports/embedding_slice_bge_m3.json#by_degree_slice.no_citation_edges.reciprocal_rank.mean-->-0.0691<!--/n--> | [−0.0863, −0.0503] | **예** |

간선 1~4개 집단(251 시드)에서도 세 지표 모두 0을 제외하고 내려갑니다. 간선 5개 이상(14 시드)은
세 지표 전부 0을 포함합니다 — **결론을 내지 않습니다.**

### 작은 형제보다도 나쁩니다

이쪽이 더 할 말이 있습니다. 11-5절이 쟀던 `bge-base-en-v1.5`는 **109M·768차원**이고 BGE-M3는
**568M·1024차원**입니다.

| 모델 | 크기 | recall@5 | OpenAI 대비 | 0 제외 |
|---|---:|---:|---:|---|
| `text-embedding-3-large` | — | 0.1227 | — | — |
| `bge-base-en-v1.5` | 109M | 0.1124 | −0.0102 | 예 |
| **`bge-m3`** | **568M** | **0.0893** | **−0.0334** | 예 |

같은 정답·같은 하네스에서 **BGE-M3를 bge-base와 직접** 재면(1,112 시드):

| 지표 | bge-base | bge-m3 | 차 | 구간 | 0 제외 |
|---|---:|---:|---:|---|---|
| recall@5 | 0.1124 | 0.0893 | <!--n:reports/embedding_slice_m3_vs_base.json#by_degree_slice.no_citation_edges.recall_at_k.mean-->-0.0232<!--/n--> | [−0.0291, −0.0171] | **예** |
| hit@5 | 0.4397 | 0.3786 | <!--n:reports/embedding_slice_m3_vs_base.json#by_degree_slice.no_citation_edges.hit_at_k.mean-->-0.0612<!--/n--> | [−0.0827, −0.0386] | **예** |
| MRR | 0.3182 | 0.2766 | <!--n:reports/embedding_slice_m3_vs_base.json#by_degree_slice.no_citation_edges.reciprocal_rank.mean-->-0.0417<!--/n--> | [−0.0583, −0.0244] | **예** |

**파라미터 5.2배, 차원 1.33배를 쓰고 더 나쁩니다.** 세 지표 모두 구간이 0을 제외합니다.

## 결정

**BGE-M3를 쓰지 않습니다.** 배포 경로를 따질 필요도 없습니다 — ADR-039가 정한 순서가
"품질을 먼저 재고, 이기면 그때 배포 경로를 따진다"였고, **지면 배포 논의가 필요 없다**고
적어 두었습니다. 지금이 그 경우입니다.

`derived/2026-10-14/local-baai-bge-m3.npy`는 남겨 둡니다. 다음 후보가 생기면 **대조 상대**가
하나 늘어난 것이고, 다시 44분을 쓸 이유가 없습니다.

## 왜 그럴 수 있는지 — 적어 두지만 측정하지 않았습니다

**이것은 가설이고 측정이 아닙니다.** BGE-M3는 100개 넘는 언어와 8,192 토큰을 겨냥한
다국어·장문 모델이고, 이 말뭉치는 **영어 전용**에 제목+초록 중앙값 258토큰입니다. 그 용량이
이 과제에 쓰이지 않는 쪽으로 배분돼 있을 수 있습니다. 영어 전용·단문에 맞춘 `bge-base`가
더 나은 것이 그 읽기와 맞습니다.

**다만 이 설명은 재지 않았습니다.** 확인하려면 다국어 모델 여럿을 같은 자리에서 재야 하고,
그 결과가 이 프로젝트의 결정을 바꾸지 않습니다. **쓸 수 없다는 것을 아는 데는 위 표가
충분합니다.**

## Qwen3-Embedding-0.6B는 아직 재지 않았습니다

ADR-039가 "잽니다"로 둔 둘 중 하나가 남았습니다. 이 결과가 그것을 **결정하지는 않습니다** —
다른 가족, 다른 학습입니다. 다만 사전 확률은 내려갑니다: 595M으로 BGE-M3와 크기가 비슷하고,
이 과제에서 크기가 도움이 되지 않는다는 관측이 하나 생겼습니다.

재는 비용은 Colab 44분 + 로컬 판정 2분이고 **API 비용이 없습니다.** 결정은 사용자에게
남깁니다. 재면 이 ADR에 결과를 덧붙입니다.

## 이 결정이 바뀌는 조건

- **과제가 바뀌면** 다시 재야 합니다. 이 바는 "미래에 함께 인용될 논문"이고, 다국어 질문이나
  장문 입력이 생기면 BGE-M3의 용량이 쓰일 자리가 생깁니다.
- **OpenAI 임베딩을 더 못 쓰게 되면** 비교의 기준이 달라집니다. 그때는 "OpenAI를 이기는가"가
  아니라 "로컬 중 무엇이 가장 나은가"이고, 그 경쟁에서는 **bge-base가 BGE-M3를 이깁니다.**

## 재현

```bash
uv run python -m evals.related_study --cases evals/cases_related_future.json \
    --variants vector --embeddings derived/2026-10-14/local-baai-bge-m3.npy \
    --output reports/embedding_bge_m3.json
uv run python -m evals.embedding_slice_study \
    --baseline reports/embedding_openai.json --candidate reports/embedding_bge_m3.json \
    --output reports/embedding_slice_bge_m3.json
uv run python -m evals.embedding_slice_study \
    --baseline reports/embedding_bge_base.json --candidate reports/embedding_bge_m3.json \
    --output reports/embedding_slice_m3_vs_base.json
```

OpenAI도 Aura도 쓰지 않습니다 — 파생 캐시와 스냅샷만 읽습니다.
