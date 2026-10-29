# ADR-097: 빌린 GPU로 임베딩 파인튜닝을 키워서 다시 잰다 — 바를 먼저 적고, 판정은 ADR-039 자리에서

- **상태:** 보류 (바를 적은 날 2026-10-29, 측정 전)
- **날짜:** 2026-10-29

## 맥락

수업용으로 RTX PRO 4500(32GB)을 빌렸습니다. 이 저장소에서 GPU가 있어야 답할 수 있고 아직 열려 있는 질문은
[ADR-013](ADR-013-linear-adapter-offline-only.md) 민감도에 남은 조건 하나입니다.

> **학습 규모** — 1 epoch, 36,554쌍이었습니다. epoch를 늘리거나 쌍을 늘리면 달라질 수 있습니다.

2026-10-16 Colab T4 실행(`reports/embedding_finetune_colab.json`)은 이랬습니다.

| | 인용 과제 recall@10 | OpenAI 대비 |
|---|---:|---|
| `text-embedding-3-large` | 0.2019 | — |
| `bge-base-en-v1.5` 튜닝 전 | 0.1909 | −0.0109 [−0.0250, +0.0032] |
| `bge-base` `inbatch` (배치 32, 1 epoch) | 0.2051 | +0.0032 [−0.0115, +0.0188], **0 포함** |

T4 15GB가 정한 제약이 둘입니다 — **배치 32**(MNR 손실에서 배치 안 음성이 31개뿐), **1 epoch**(3시간). 32GB면 둘 다
풀립니다.

**판정 자리도 그사이 바뀌었습니다.** 위 표는 인용 과제(365질의)입니다. 그 뒤
[ADR-039](ADR-039-measure-embeddings-where-the-signal-is-only-the-embedding.md)가 임베딩을 재는 자리를 **미래 라벨 과제의
간선 0개 집단**(1,112 시드)으로 정했고, 인용 과제에서 "동등"이던 튜닝 전 bge-base가 거기서는 구간이 0을 제외하고
졌습니다. **2026-10-16에 튜닝한 모델은 그 자리에서 한 번도 재지 않았습니다.**

## 누출 — 학습 쌍은 예측 시점에 보이던 정보뿐입니다

| | 자르는 선 |
|---|---|
| 학습 쌍 (`export_for_colab`) | 인용자 `< 2026-08` (`split_citers`, `DEFAULT_CUT = "2026-08"`) |
| 미래 과제 피처 (`cases_related_future.json`) | `feature_cut = "2026-08"` |
| 미래 과제 정답 | `2026-08` 이후 인용자가 만든 **새** 공동인용 |

학습이 보는 인용은 미래 과제가 피처로 쓰는 그래프와 같은 범위입니다. 그리고 **간선 0개 집단의 시드는 그 그래프에 인용
간선이 없으므로** 학습 쌍의 앵커로도 양성으로도 나오지 않습니다. 모델은 시드에 대해 직접 배운 것이 없습니다.

## 결정 — 무엇을 돌리는가

노트북 [`notebooks/finetune_scale_runpod.ipynb`](../../notebooks/finetune_scale_runpod.ipynb) 하나로 학습과 인코딩을
같이 합니다. 판정은 저장소에서 기존 하네스로 합니다.

**변형은 셋이고, 이것이 전부입니다.** 결과를 본 뒤 늘리지 않습니다.

| 이름 | 무엇 | 역할 |
|---|---|---|
| `b256-e3` | 배치 256, 3 epoch | **주 후보.** 이것만 채택 판정을 받습니다 |
| `b32-e3` | 배치 32, 3 epoch | 귀속용 — epoch만 늘린 효과 |
| `inbatch-2026-10-16` | 기존 HF 모델을 인코딩만 | 귀속용 — 옛 모델이 ADR-039 자리에서 어디인지 |

공통 조건: `BAAI/bge-base-en-v1.5`, 512토큰, `CachedMultipleNegativesRankingLoss`, 학습률 2e-5, 워밍업 10%, 씨앗
20261015, 하드 네거티브 없음(ADR-013에서 두 번 기각), **`NO_DUPLICATES` 배치 샘플러.** 마지막 것은 새로 넣습니다 — 한
앵커가 참고문헌 여러 편과 쌍을 이루므로, 배치가 커지면 같은 앵커의 다른 양성이 **음성으로** 들어올 일이 많아집니다.

**epoch를 고르지 않습니다.** 최종 모델은 3 epoch 끝 모델입니다. epoch마다 인용 과제 점수를 찍지만 진단용이고, 그것을
보고 중간 모델을 고르면 평가 질의로 모델을 고른 것이 됩니다.

**귀속용 두 변형은 채택 근거가 아닙니다.** 셋 중 하나라도 통과하면 채택하는 방식은 비교를 세 번 하는 것이라, 우연한
통과를 늘립니다. 주 후보 하나만 판정합니다.

## 바 — 재기 전에 적습니다

**자리:** 미래 라벨 과제(`evals/cases_related_future.json`), `vector` 변형, **간선 0개 집단 1,112 시드**. 기준은
`text-embedding-3-large`(`reports/embedding_openai.json`)입니다.

| 지표 | OpenAI | 튜닝 전 bge-base | **`b256-e3`이 넘어야 하는 선** |
|---|---:|---:|---|
| recall@5 | **0.1227** | 0.1124 | 평균 차가 양수이고, 쌍체 95% 구간이 **0을 제외** |
| hit@5 | 0.4739 | 0.4397 | 구간 전체가 0 아래이면 안 됨 |
| MRR | 0.3457 | 0.3182 | 구간 전체가 0 아래이면 안 됨 |

**부가 조건:** 간선 1~4개 집단(251 시드, OpenAI recall@5 0.2154)에서 recall@5 구간 전체가 0 아래이면 안 됩니다.

**동점은 통과가 아닙니다.** 동점이면 OpenAI 임베딩이 이미 배포돼 있으므로 바꿀 이유가 없습니다.

**의심할 수 있게 상한을 적습니다.** 이 과제 recall@5의 상한이 0.783(`recall_at_k_ceiling`)입니다. 그보다 크면 측정이
틀린 것입니다. 현실적으로는 OpenAI보다 +0.03 넘게 오르면(튜닝 전과의 차이 0.0102의 세 배) 행 순서부터 의심합니다.

**인용 과제(365질의)는 노트북 안에서 진단으로만 씁니다.** 2026-10-16과 같은 질의로 재므로 학습이 되었는지는 보여
주지만, 판정 자리가 아닙니다.

## 노트북을 넘기기 전에 확인한 것 (2026-10-29)

- **SMOKE를 로컬 CPU로 끝까지 돌렸습니다.** 코드 셀 13개를 순서대로 실행하는 하네스로, 학습 → epoch별 채점 → 세 행렬
  인코딩 → 보고서 → 업로드 호출까지 통과했습니다. HF 업로드는 가짜로 바꿨고, 비공개 옛 모델은 토큰 없이 못 받아 공개
  기본 모델로 대신했습니다. 그때 쓴 판은 sentence-transformers 6.2.0, transformers 5.16.1입니다(`-U`로 RunPod에 깔릴 판).
- **`export_for_colab`을 다시 돌려 학습 쌍 36,554개, OpenAI recall@10 0.2019가 2026-10-16과 같게 나왔습니다.**
- **`corpus.jsonl.gz`의 순서가 `derived/2026-10-14/embedding_ids.json`과 37,854행 모두 같습니다.** 노트북이 만드는 행렬을
  `--embeddings`로 그대로 넣을 수 있습니다.

## 이기면 무엇이 남는가

**배포는 이 ADR이 정하지 않습니다.** ADR-049가 적은 순서 그대로 — 품질을 먼저 재고, 이기면 그때 배포 경로를 따집니다.
이기더라도 남는 비용은 이렇습니다.

- 질의 시 로컬 모델 추론. 지금 VM(e2-small급)은 GPU가 없고, torch가 앱 의존성에 들어옵니다.
- 37,854편 재임베딩과 Aura 벡터 인덱스 교체(차원 768로 같으므로 인덱스 정의는 그대로).
- 새 논문을 넣는 주간 수집도 같은 모델로 인코딩해야 합니다.

## 기록

학습 곡선은 실행 중인 팟 밖에 남깁니다 — DagsHub MLflow(`arxiv-graphrag-embedding` 실험, [ADR-014](ADR-014-remote-mlflow-tracking.md))
또는 TensorBoard 이벤트 파일을 HF에 올리는 쪽. 팟 안 CSV는 쓰지 않습니다. 모델 가중치는 HF 비공개 저장소에 두고,
저장소에는 보고서와 판정 결과만 커밋합니다.

## 민감도

- **주 후보가 통과해도 귀속용이 함께 통과하면**, 무엇이 이득을 만들었는지 나눌 수 있습니다(epoch인가 배치인가).
  주 후보만 통과하면 배치 크기가 이유라고 적을 수 있습니다.
- **주 후보가 지면** ADR-013의 "학습 규모" 조건이 닫히고, ADR-009(OpenAI 임베딩 유지)의 근거가 "재 봤고 없다"에서
  "키워서도 재 봤고 없다"가 됩니다. 남는 조건은 과제 정식화(음성을 뽑는 규칙)뿐입니다.
- **인용 과제에서는 이기고 미래 과제에서 지면**, 학습이 "논문이 인용할 논문"에 맞춰졌고 "앞으로 함께 인용될 논문"으로는
  옮겨지지 않는다는 뜻입니다. 이 경우 다음 후보는 공동인용 쌍으로 학습하는 것입니다.

## 참고

- 노트북 [`notebooks/finetune_scale_runpod.ipynb`](../../notebooks/finetune_scale_runpod.ipynb) · 학습 데이터
  [`models/embedding_tuning/export_for_colab.py`](../../models/embedding_tuning/export_for_colab.py)
- 판정 [`evals/related_study.py`](../../evals/related_study.py) · [`evals/embedding_slice_study.py`](../../evals/embedding_slice_study.py)
- [ADR-013](ADR-013-linear-adapter-offline-only.md) · [ADR-039](ADR-039-measure-embeddings-where-the-signal-is-only-the-embedding.md) ·
  [ADR-049](ADR-049-no-local-embedding-clears-the-bar.md) · [EXPERIMENTS 11-6절](../../EXPERIMENTS.md)
