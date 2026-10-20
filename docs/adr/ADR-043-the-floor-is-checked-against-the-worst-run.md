# ADR-043: RAGAS 하한은 최악의 정상 실행과 심은 결함으로 검사한다 — 표본은 더 늘리지 않습니다

- **상태:** 채택
- **날짜:** 2026-10-19

## 맥락 — 전제가 낡아 있었습니다

ROADMAP 11-4(2026-10-16)가 RAGAS에 대해 이렇게 적어 두었습니다.

> 남은 것은 **표본**입니다. 지금 5건이고, 임계값을 정하려면 더 필요합니다.

그 문장을 근거로 "표본을 늘린다"를 작업으로 세웠는데, **세기 전에 확인하니 이미 지나간
이야기였습니다.**

| | ROADMAP 11-4가 적은 것 | 실제 |
|---|---|---|
| 사례 | 5건 | **30건** (가족마다 6건) |
| 실행 | — | 기준선 3회, 저장소 전체 **12회** |
| 변동의 종류 | — | 판정만 / 전체를 **이미 나눠 쟀습니다**(`ragas_spread.py`) |

**낡은 메모를 근거로 API 비용을 쓸 뻔했습니다.** 그래서 작업을 바꿨습니다 — 더 재는 것이
아니라, **이미 가진 것으로 하한이 하한 노릇을 하는지** 검사합니다.

## 찾은 결함 — 근거가 다른 양에서 계산되고 있었습니다

`ragas_spread.py`가 세던 `context_precision`은 **다섯 가족 전부의 평균**입니다. 그런데
게이트의 하한은 `ragas_context_precision_stable_families`, 즉 **`author`·`topcited` 둘만의
평균**에 걸려 있습니다.

| 양 | 값 |
|---|---:|
| 전체 가족 평균 `context_precision` | 약 **0.70** |
| 게이트가 보는 안정 가족 평균 | **1.0000** |

전자의 변동폭을 후자의 하한 근거로 읽으면 **다른 양을 근거로 삼는** 것입니다. 이 프로젝트에서
"재고 있는 것이 생각한 것과 다르다"가 나온 또 한 자리입니다(ADR-033의 알파벳, ADR-021의
Answer Relevancy).

그래서 `--floors`가 **게이트가 보는 양으로** 셉니다 — `evals.gate.ragas_metrics`를 그대로
불러 가족을 좁힌 뒤 집계합니다. 두 구현이 갈라지면 그것이 다시 같은 함정이 됩니다.

## 결정 — 하한에 묻는 질문은 둘입니다

[ADR-004](ADR-004-quality-gate-thresholds.md)가 "하한은 재는 것이 아니라 파국을 잡는 것"이라고
적었습니다. 그 문장을 검사 가능한 형태로 바꿉니다.

1. **정상이던 실행 중 최악이 하한을 넘는가.** 넘지 못하면 그 하한은 품질이 아니라 변동으로
   터집니다. 집단별 평균은 **같은 코드 상태**끼리만 내지만, 최악값은 **집단을 넘어** 봅니다 —
   한 번 정상이었던 점수에서 터지는 하한은 쓸 수 없기 때문입니다.
2. **결함을 심으면 걸리는가.** 안 걸리면 그 지표는 상수이고, 상수에 걸린 하한은 게이트가
   아닙니다. ADR-021이 같은 이유로 `context_relevance`를 뺐습니다 — 원본 30건과 결함 20건,
   **50건 전부 정확히 1.0000**이었습니다.

## 측정 — 12회, 세 코드 상태

| 집단 | 실행 | `ragas_faithfulness` 변동폭 | 안정 가족 precision |
|---|---:|---|---:|
| 기준선 (resolve) | 3 | 0.9656 ~ 1.0000 (**0.0344**) | 1.0000 (0.0000) |
| routed | 3 | 0.9833 ~ 1.0000 (**0.0167**) | 1.0000 (0.0000) |
| `ragas_study_full` | 6 | 0.9000 ~ 0.9917 (**0.0917**) | 1.0000 (0.0000) |

### 판정

| | `ragas_faithfulness` | 안정 가족 precision |
|---|---:|---:|
| 하한 | 0.80 | 0.85 |
| 본 실행 | <!--n:reports/ragas_floor_basis.json#verdict.ragas_faithfulness.runs_seen-->12<!--/n--> | 12 |
| **최악 정상 실행** | **<!--n:reports/ragas_floor_basis.json#verdict.ragas_faithfulness.worst_normal_run-->0.9000<!--/n-->** | **<!--n:reports/ragas_floor_basis.json#verdict.ragas_context_precision_stable_families.worst_normal_run-->1.0000<!--/n-->** |
| 하한까지 여유 | +<!--n:reports/ragas_floor_basis.json#verdict.ragas_faithfulness.margin_to_floor-->0.1000<!--/n--> | +<!--n:reports/ragas_floor_basis.json#verdict.ragas_context_precision_stable_families.margin_to_floor-->0.1500<!--/n--> |
| 결함을 심으면 | **<!--n:reports/ragas_floor_basis.json#verdict.ragas_faithfulness.with_a_planted_defect.0-->0.4025<!--/n--> / <!--n:reports/ragas_floor_basis.json#verdict.ragas_faithfulness.with_a_planted_defect.1-->0.4192<!--/n-->** | **<!--n:reports/ragas_floor_basis.json#verdict.ragas_context_precision_stable_families.with_a_planted_defect.0-->0.5000<!--/n--> / 0.5000** |
| 그때 걸리는가 | **예** | **예** |

**둘 다 통과합니다.** 최악의 정상 실행에서도 터지지 않고, 결함을 심으면 걸립니다.

안정 가족 precision이 12회 전부 **정확히 1.0000**이라 `context_relevance`와 같은 모양으로
보였습니다. **갈라지는 지점이 결함 실험입니다** — 사례별 값이 `[0.0, 1.0, 0.0, 1.0]`이 되어
집계가 0.5000으로 내려갑니다. `context_relevance`는 결함에서도 1.0000이었습니다. 그래서 하나는
남고 하나는 빠집니다.

## 따라온 결론 — 표본을 늘리지 않습니다

기준선이 적은 변동폭 **0.0344는 3회의 값**이고, 12회로 보면 `ragas_faithfulness`의 관측 범위는
**0.1000**입니다(3배). 그래서 기준선의 `observed_spread`는 **그대로 둡니다** — 그 값은
"기준선을 만든 세 실행이 보인 것"이라는 사실이고, 그 사실이 틀린 것은 아닙니다. 더 넓은 근거는
`floor_rationale`과 `reports/ragas_floor_basis.json`에 따로 적습니다.

그리고 **새 RAGAS 실행을 돌리지 않습니다.** 돌려서 바뀌는 결정이 없습니다 — 하한은 최악의
관측에서 0.1000 남고, 결함 실험이 두 하한 모두 걸린다고 말합니다. 전체 실행 1회가 토큰
약 42만·7~8분이고(ROADMAP 8-4) 재서 결정이 바뀌지 않는 측정에 쓸 이유가 없습니다.

**철회 조건을 적어 둡니다.** 정상 실행이 하한에서 0.05 안으로 들어오거나, 결함 실험에서
어느 하한이 걸리지 않으면 다시 봅니다. 후자면 늘릴 것은 표본이 아니라 **그 지표를 게이트에서
빼는 결정**입니다(`floor_rationale`이 이미 그렇게 적어 둔 그대로).

## 화면에도 올립니다

품질 게이트 탭의 접은 칸 하나가 이 표를 그대로 그립니다 — 하한, 본 실행 수, 최악 정상 실행,
결함을 심었을 때의 값, 그때 걸리는가. **하한이 왜 그 값인지**를 글이 아니라 숫자로 보이는
자리입니다.

## 재현

```bash
uv run python -m evals.ragas_spread --floors baselines/eval_baseline.json \
    --group "기준선(resolve)=reports/baseline_resolve_run1_ragas.json,reports/baseline_resolve_run2_ragas.json,reports/baseline_resolve_run3_ragas.json" \
    --group "routed=reports/baseline_routed_run1_ragas.json,reports/baseline_routed_run2_ragas.json,reports/baseline_routed_run3_ragas.json" \
    --group "ragas_study_full=reports/ragas_study_full*.json" \
    --perturbed reports/ragas_perturbed_luna.json reports/ragas_perturbed_sol.json \
    --output reports/ragas_floor_basis.json
```
