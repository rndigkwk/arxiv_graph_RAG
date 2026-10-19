# ADR-033: 순위 지표는 알파벳을 재고 있었다 — 순서에 의존하지 않는 지표를 더한다

- **상태:** 채택
- **날짜:** 2026-10-19

## 맥락

[ADR-024](ADR-024-route-by-question-type.md)가 `retrieved_ids`의 **순서 문제**를 다음 기준선
주기 후보로 적어 뒀습니다. "중간 조회 결과가 목록 1위를 차지해 정답을 2위로 민다"는 것이었고,
기준 논문을 빼는 것으로 `reciprocal_rank`가 0.6963에서 0.9889로 올랐습니다. 남은 것은 "목록을
어떤 순서로 이어 붙일지 다시 정하는 일"이라고 적었습니다.

**그 일을 하려고 먼저 쟀더니, 고칠 순서가 없었습니다.**

## 측정

저장된 기준선 3회분(90 사례-실행)을 재채점했습니다. API를 쓰지 않습니다 — `retrieved_ids`에
집합 연산을 다시 적용한 것이 전부여서, 모델의 비결정성이 결론에 섞이지 않습니다
([`evals/retrieval_metric_study.py`](../../evals/retrieval_metric_study.py)).

| 무엇 | 측정 |
|---|---|
| `retrieved_ids` 집합 == 골드 집합 | **30/30** (초과 0건, 누락 0건) |
| 3회 실행에서 순서가 같은가 | 30/30 |
| 답변 조회가 무엇으로 정렬했나 | **식별자 24건**(`title`·`arxiv_id`), 점수 6건(`citation_count DESC`) |

두 줄이 함께 뜻을 만듭니다.

- 목록이 **전부 골드**이므로 1위가 무엇이든 `reciprocal_rank`는 1.0입니다.
- 그 1위를 고른 것은 24건에서 **제목이나 arxiv_id의 첫 글자**입니다.

즉 `reciprocal_rank` <!--n:reports/retrieval_metric_study.json#generations.2.graph_rag.overall.reciprocal_rank-->1.0<!--/n-->은
"순위가 좋다"가 아니라 **"조회가 골드 집합을 정확히 돌려줬고, 그것을 알파벳순으로 줄 세웠다"**
입니다. `hit_at_k`·`recall_at_k`도 같은 사실의 다른 표현입니다. **순서를 바꿔도 세 지표는
움직이지 않습니다** — 바꿀 순서가 애초에 품질이 아니었기 때문입니다.

점수순으로 정렬하는 6건은 `topcited` 가족입니다. 그 가족에서만 "1위"가 뜻을 가지는데, 하필
그 질문은 **정렬 자체가 정답**입니다("인용이 가장 많은 논문"). 순위 품질을 재는 것이 아닙니다.

## 결정 1 — 세 지표를 지우지 않고, 뜻을 고쳐 적는다

지울 수도 있었습니다. 지우지 않는 이유는 **검색이 깨지면 내려간다**는 것입니다. 같은 보고서의
`vector_only` 대조가 그것을 보여 줍니다 — 그래프 질문을 벡터 경로로 보내면 `hit_at_k`가
<!--n:reports/retrieval_metric_study.json#vector_only_contrast.overall.hit_at_k-->0.0333<!--/n-->입니다.
회귀 탐지기로는 유효하고, **품질 지표로는 아닙니다.** 그 구별을 docstring과 기준선의
`floor_rationale`에 적었습니다.

올리려고 노력할 대상에서는 뺍니다. ADR-024가 "다음 주기에 순서를 고친다"고 적은 항목은
**고칠 것이 없다는 측정으로 닫습니다.**

## 결정 2 — 순서에 의존하지 않는 지표를 더한다

`retrieval_precision`·`retrieval_recall`입니다. 자른 목록이 아니라 **전체 목록**의 집합
연산입니다.

필요한 이유는 측정이 정했습니다. 라우팅 전(2026-10-16) `coauthor` 가족이 hit@5
**0.1667**이었는데, 같은 보고서의 집합 recall은 **1.0000**입니다. 골드를 다 찾아 놓고 평균
7.3편을 함께 돌려줘서 알파벳순 상위 5개 밖으로 밀린 것입니다. `recall_at_k` 하나로는
**"못 찾았다"와 "너무 많이 찾았다"가 같은 0.1667**으로 보입니다.

| 상태 | `retrieval_precision` | `retrieval_recall` | `recall_at_k` | `hit_at_k` |
|---|---:|---:|---:|---:|
| 2026-10-16 라우팅 전 | <!--n:reports/retrieval_metric_study.json#generations.0.graph_rag.overall.retrieval_precision-->0.7908<!--/n--> | <!--n:reports/retrieval_metric_study.json#generations.0.graph_rag.overall.retrieval_recall-->0.9667<!--/n--> | 0.7111 | 0.7333 |
| 2026-10-18 라우팅 후 | 0.8244 | 1.0000 | 0.9933 | 1.0000 |
| 2026-10-19 `resolve_paper` 후 (제품) | <!--n:reports/retrieval_metric_study.json#generations.2.graph_rag.overall.retrieval_precision-->1.0<!--/n--> | 1.0000 | 1.0000 | 1.0000 |
| `vector_only` 대조 (경로가 사라진 상태) | <!--n:reports/retrieval_metric_study.json#vector_only_contrast.overall.retrieval_precision-->0.0083<!--/n--> | 0.0111 | 0.0111 | 0.0333 |

**빈 목록의 precision은 0.0입니다.** `score_related`가 None을 돌려주는 것과 다릅니다 — 관련
패널은 골드가 없으면 채점하지 않는 선택 기능이지만, 검색이 아무것도 못 돌려준 것은 이 경로의
실패입니다. None으로 두면 `summarize`가 건너뛰어 **전면 실패가 집계에서 사라집니다.**

## 결정 3 — 하한과 허용폭을 집합 지표에 둔다

| | 하한 | 허용 하락폭 |
|---|---:|---:|
| `retrieval_precision` | 0.70 | 0.05 |
| `retrieval_recall` | 0.90 | 0.05 |

하한은 위 표의 **재난 줄**에 맞췄습니다 — 그래프 질문이 그래프 경로로 가지 않는 상태(0.0083 /
0.0111)를 확실히 잡고, 라우팅 전의 섞인 상태(0.7908 / 0.9667)는 잡지 않습니다. 그 상태는
나빴지만 답변 근거는 1.000이었고 이미 고쳤습니다. 측정값 1.0000과 같게 두지 않는 이유는
[ADR-004](ADR-004-quality-gate-thresholds.md)와 같습니다.

**허용폭은 순위 지표에는 없고 집합 지표에는 있습니다.** 차이는 측정된 변동폭입니다 — 집합
지표는 90 사례-실행 전부에서 1.0000이고 변동폭 **0.0000**입니다. 순위 지표에 허용폭을 두지
않은 근거(기준선의 `no_drop_on_rank_metrics`)가 "변동이 커서"였으므로, 변동이 0인 지표에는
둘 수 있습니다.

### 돌려서 확인했습니다

말로 적지 않고 **쓰레기를 심어** 재 봤습니다. 심는 쓰레기는 같은 보고서의 `vector_only`에서
가져옵니다 — 가짜 ID를 만들면 "존재하지 않는 논문"을 심는 것이고, 실제 회귀는 **진짜지만
무관한 논문**이 섞이는 모양이었습니다(ADR-024).

| 초과 조회 사례 | `retrieval_precision` | `hit_at_k` | 게이트 |
|---:|---:|---:|---|
| 0건 | 1.0000 | 1.0000 | 통과 |
| 2건 | 0.9554 | 1.0000 | 통과 |
| 3건 | 0.9345 | 1.0000 | **drop** |
| 30건 (전부) | <!--n:reports/retrieval_metric_study.json#floor_demo.5.metrics.retrieval_precision-->0.3474<!--/n--> | 0.9000 | **floor + drop** |

**마지막 줄이 이 결정의 값어치입니다.** 30건 전부가 무관한 논문 5편을 함께 돌려주는 상태에서
`hit_at_k`는 0.9000, `recall_at_k`는 0.5844이고 **둘 다 자기 하한(0.5 / 0.48) 안입니다.**
집합 precision만 하한을 깹니다. 이 상태를 잡는 규칙이 전에는 없었습니다.

3건에서 걸리는 것도 예측과 맞았습니다 — 허용폭 0.05의 뜻을 "사례 세 건이 초과 조회를
시작했다"로 적어 두었고, 2건 통과·3건 실패로 그대로 나왔습니다.

## 과정에서 막힌 것

**기준선을 다시 쓰면서 `model_registry`가 조용히 사라졌습니다.** [ADR-032](ADR-032-close-the-model-lifecycle.md)가
넣은 키인데 `evals/gate.py`의 "들고 올 키 목록"에 없었습니다. `splits`가 한 번,
`related_slot`이 한 번, 이번이 **세 번째**입니다.

세 번째를 겪고 방향을 뒤집었습니다. 목록을 들고 있으면 **빠뜨릴 수 있습니다.** 이제
`make_baseline`이 스스로 계산하는 키(`DERIVED_KEYS`)만 적고, 나머지는 전부 들고 옵니다. 더하는
것을 잊어도 키는 남습니다.

같은 테스트도 고쳤습니다. 전 판은 `CARRIED_OVER`로 `carried`를 만들고 같은 목록이 남았는지
확인해서 **목록에서 빠뜨린 키를 영원히 잡지 못했습니다.** 이제 기준선 파일에 실제로 있는 키를
기준으로 봅니다.

**판정 모델이 들고 오는 값이라는 것도 그때 드러났습니다.** `ragas_judge_model`은 새 실행에서
측정되는데 `make_baseline`이 그것을 쓰지 않고 옛 기준선 값을 들고 왔습니다. 다른 모델로 채점한
실행으로 기준선을 쓰면 **옛 모델 이름이 남아 다음 게이트가 영원히 불일치로 막힙니다.** 어느
쪽이 맞는지 코드가 모르므로, 값이 다르면 추측하지 않고 멈추게 했습니다.

**초과 조회 실험을 처음에 틀리게 짰습니다.** 심은 사례만 재채점해서, 집합 지표가 그 사례에만
생기고 30건 평균이 **심은 건수의 평균**이 됐습니다. "초과 조회 1건에 precision 0.2857"이라는
값이 나와서 알았습니다 — 1건을 심었는데 전체 평균이 0.29일 수는 없습니다.

## 아직 아닌 것

**순위 품질을 재는 평가가 없습니다.** 이 하네스에서 순위가 뜻을 가지려면 조회가 **골드보다 많이**
돌려주고 그것을 relevance로 줄 세워야 합니다. 지금 30건은 조회가 골드 집합을 정확히 돌려주므로
그 조건 자체가 성립하지 않습니다. 재정렬을 재고 싶으면 사례를 그렇게 만들어야 합니다
([ADR-029](ADR-029-ppr-beats-the-adapter-in-the-related-slot.md)가 관련 패널에서 같은 벽을 만났습니다).

**`retrieved_ids`의 순서는 여전히 조회 순서 + Cypher의 `ORDER BY`입니다.** 모델이 인용한
순서(`evidence_ids`)로 다시 세우는 방법을 생각했지만, 그러면 `reciprocal_rank`가 **생성기의
인용 순서**를 재게 되고 그것은 `answer_evidence_precision`이 이미 재고 있습니다. 같은 것을
두 번 세는 지표는 더하지 않습니다.

## 재현

```bash
uv run python -m evals.rescore --report reports/baseline_resolve_run1.json --suffix _setmetrics
uv run python -m evals.retrieval_metric_study
```
