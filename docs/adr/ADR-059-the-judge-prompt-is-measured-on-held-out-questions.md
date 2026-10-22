# ADR-059: 심판 프롬프트는 보류 질문에서 잰다

- **상태:** 채택
- **날짜:** 2026-10-22

## 맥락

[ADR-058](ADR-058-the-independent-judge-only-means-something-with-labels.md)이 공급자를 교차해
`requires_content`가 **공급자 의존**이라는 것을 쟀습니다(13건, p=0.0002). 그리고 스스로 한계를
적어 뒀습니다.

> 이 61문항은 프롬프트를 맞춰 넣은 바로 그 집합입니다. (…) 더 말하려면 **튜닝에 쓰지 않은
> 질문**이 필요하고, 그건 이 ADR의 범위가 아닙니다.

그 한계가 두 가지를 막고 있습니다.

1. `requires_graph`의 "전사됨"이 결론이 못 됩니다 — 맞춰 넣은 집합에서 둘 다 만점인 것뿐입니다.
2. **그리고 더 큰 것**: OpenAI의 `61/61`이 **프롬프트의 성질인지 튜닝 집합의 성질인지** 아직
   구별되지 않았습니다. ADR-058은 그것을 "`gpt-6-luna`의 성질"이라고 적었지만, 셋째 가능성이
   남아 있습니다 — **두 공급자 다 튜닝 집합 밖에서는 떨어질 수 있습니다.**

[ROADMAP 11-5](../../ROADMAP.md)의 GEPA가 막혀 있던 이유도 여기 닿습니다 — *"최적화 대상이
무엇인지 먼저 정해야 합니다"*. ADR-058이 그 대상을 만들었습니다(v3 문장은 기울기이고
`exact-title`과 그래프 가족이 반대로 당깁니다). 그런데 **보류 집합 없이 프롬프트를 고치면 그
개선은 측정이 아닙니다.**

## 결정

**보류 질문을 기계로 생성하고, 거기서 재고, 그 결과에 따라서만 프롬프트를 손댑니다.**

라벨 규칙이 질문 문장에서 기계적으로 나오므로(`judge_study.labelled_questions`) **사람 라벨 없이
보류 집합을 만들 수 있습니다.** 이것이 이 과제를 가능하게 하는 유일한 이유입니다 — 라벨이 사람
판단이면 보류 집합은 비용 문제로 막힙니다.

생성은 기존 질문의 **템플릿을 그대로** 씁니다. 모양이 다른 질문을 만들면 다른 것을 재게 됩니다.

| 가족 | 템플릿 | `requires_content` | `requires_graph` |
|---|---|---|---|
| `exact-title` | `Find the paper titled: {제목}` | True | False |
| `author` | `Which papers in this collection list an author named {이름}?` | False | True |
| `citing` | `Which papers in this collection cite the paper titled {제목}?` | False | True |
| `coauthor` | `Which other papers in this collection share an author with {제목}?` | False | True |
| `reference` | `Among the papers cited by {제목}, which ones have primary category {분류}?` | False | True |
| `topcited` | `Within primary category {분류}, which {N} papers are cited most often by other papers in this collection?` | False | True |

**`topic` 가족은 생성하지 않습니다.** "그래프 신경망"처럼 주제 구절을 고르는 일은 기계적이지
않습니다. 그래서 `expect_content=True`인 두 가족 중 **`exact-title`만** 보류 집합에 들어갑니다.
ADR-058의 실패가 그 가족에서 났으므로 측정에는 충분하지만, **`topic`에 대해서는 이 연구가
아무것도 말하지 않습니다.**

### 보류 집합은 평가 사례가 아닙니다

생성한 질문에는 스냅샷 Cypher로 센 정답 `expected_ids`가 **없습니다.** 심판 판정(`requires_*`)의
라벨만 있습니다. 그러므로 이 파일을 `evals/cases*.json`처럼 회귀 게이트에 쓰면 안 됩니다. 파일
이름과 매니페스트에 그 사실을 적습니다.

### 두 조각으로 나눕니다

프롬프트를 고치는 데 쓴 질문은 더 이상 보류가 아닙니다. 그래서 생성 즉시 나눕니다.

| 조각 | 몫 | 용도 |
|---|---:|---|
| **진단** | 2/3 | 기울기가 실재하는지 보고, 프롬프트를 고칠 때 본다 |
| **최종 확인** | 1/3 | **프롬프트를 고치는 동안 보지 않는다.** 마지막에 한 번만 |

최종 확인 조각을 한 번이라도 보고 프롬프트를 고치면, 그 조각도 튜닝 집합이 됩니다. 그때는
**"보류 집합이 소진됐다"고 적고** 다음 변경에는 새 질문을 만듭니다.

## 바 — 재기 전에 적습니다

### "유지"와 "떨어짐"의 경계

틀린 **비율**로 봅니다. 두 경계 모두 이 프로젝트가 이미 쓰는 값에서 가져옵니다 — 여기서 새로
만들면 결과를 보고 고르게 됩니다.

| 틀린 비율 | 판정 | 근거 |
|---|---|---|
| ≤ **0.05** | **유지** | 튜닝 집합의 0건과 구별되지 않는 구간입니다. 3의 법칙으로 0/61의 95% 상한이 `3/61 = 0.049`입니다 |
| 0.05 ~ 0.20 | **구별 못 함** | — |
| > **0.20** | **떨어짐** | ADR-055가 쓰는 허용 한계(`TOLERABLE_RED_RATE = 0.20`)와 같은 값입니다 |

공급자 사이의 비교는 ADR-058의 바를 그대로 씁니다 — 갈린 쌍이 **한 방향 6건 이상**이면
"다르다", 5건 이하는 "구별 못 함"(양측 부호검정, `needed_one_way()`가 세어 구합니다).

### 세 갈래를 미리 적습니다

진단 조각의 `requires_content`에서:

| OpenAI | Claude | 결론 | 그 다음 |
|---|---|---|---|
| 유지 | 떨어짐 | **ADR-058이 맞습니다** — 공급자 의존이 튜닝 집합 밖에서도 실재합니다 | 양쪽을 통과하는 프롬프트를 시도합니다 |
| **떨어짐** | 떨어짐 | **ADR-058의 해석을 고쳐야 합니다.** `61/61`은 `gpt-6-luna`의 성질이 아니라 **튜닝 집합에 맞춘 값**이었습니다. 공급자 문제가 아니라 프롬프트 문제입니다 | 고치면 양쪽이 같이 좋아집니다. ADR-058에 뒤집힘을 적습니다 |
| 유지 | 유지 | 오늘의 13건은 **그 21개 제목의 성질**이고 일반화되지 않습니다 | 프롬프트를 손대지 않습니다 |

가운데 줄이 중요합니다. **그 가능성을 적어 두지 않으면 첫째 줄로 읽게 됩니다** — ADR-058을 쓴
사람이 나이기 때문입니다.

### 프롬프트를 바꿀 조건

새 프롬프트는 **네 조건을 모두** 넘길 때만 채택합니다.

1. 최종 확인 조각에서 두 공급자 모두 `requires_content` 틀린 비율 **≤ 0.05**
2. 최종 확인 조각에서 갈린 쌍 **6건 미만**
3. `requires_graph`가 두 공급자 모두 **유지**(기울기의 반대쪽으로 넘어가지 않았습니다 — v2의
   거짓 양성 16건이 그 방향입니다)
4. 기존 61문항에서 **회귀 없음**(OpenAI `61/61` 유지)

하나라도 못 넘기면 **원래 프롬프트를 둡니다.** 넷 중 셋만 넘긴 프롬프트를 "거의 됐다"로 넣지
않습니다.

### 생성이 거부하는 것, 그리고 거부한 개수를 적습니다

기계 생성의 위험은 **라벨이 틀린 질문**입니다. 제목이 동시에 주제 구절로 읽히면
`expect_content=True`라는 라벨이 의심스러워집니다. 그래서 거부 조건을 둡니다.

| 거부 | 이유 |
|---|---|
| 제목이 5단어 미만 | 짧은 제목은 주제 구절로 읽힙니다(`Graph Neural Networks`) |
| 제목이 기존 `topic` 질문의 주제 구절을 품음 | 같은 문장이 두 가족의 라벨을 동시에 가집니다 |
| 저자 이름이 한 토큰 | 이름인지 단어인지 갈립니다 |
| 튜닝 집합이 쓴 논문·저자·`(분류, N)` | 보류가 아닙니다 |
| 관계가 실제로 없음(`citing`·`coauthor`·`reference`) | 질문이 공허해지고, 심판이 보는 것이 달라집니다 |

**거부한 개수를 매니페스트에 적습니다.** 거부가 많으면 생성 규칙이 집합을 한쪽으로 기울이고
있다는 뜻이고, 그것을 모르면 "보류 집합에서도 같았다"를 잘못 읽습니다.

## 결과 1: 진단 조각 — ADR-058이 맞았습니다 (2026-10-22)

생성기가 120문항을 만들었습니다(목표 전부 충족, 거부 36건 — 34건은 튜닝 집합의 논문,
2건은 한 토큰 이름). 진단 80 / 최종 40으로 가족 비율을 유지한 채 나눴습니다.

진단 조각에서 두 공급자를 같은 날 연달아 돌렸습니다.

| 진단 80문항 | OpenAI `gpt-6-luna` | Claude `claude-haiku-5-5` |
|---|---:|---:|
| `requires_content` | <!--n:reports/judge_holdout_openai.json#summary.content_accuracy-->1.0000<!--/n--> | <!--n:reports/judge_holdout_claude.json#summary.content_accuracy-->0.7625<!--/n--> |
| `requires_graph` | <!--n:reports/judge_holdout_openai.json#summary.graph_accuracy-->1.0000<!--/n--> | <!--n:reports/judge_holdout_claude.json#summary.graph_accuracy-->1.0000<!--/n--> |
| 틀린 비율 (`requires_content`) | **0.0000 → 유지** | **0.2375 → 떨어짐** |
| 갈린 쌍 | — | <!--n:reports/judge_holdout_claude.json#comparison.requires_content.only_new_wrong-->19<!--/n-->건, p ≈ 0 |

**세 갈래 중 첫째 줄입니다.** 미리 적은 표대로 읽으면:

> OpenAI 유지 / Claude 떨어짐 → **ADR-058이 맞습니다** — 공급자 의존이 튜닝 집합 밖에서도
> 실재합니다.

그리고 가운데 줄이 **배제됐습니다.** `gpt-6-luna`는 **처음 보는 질문 80개에서 80/80**입니다.
그러므로 `61/61`은 튜닝 집합에 맞춰 넣은 값이 아니고, ADR-058의 "그 모델의 성질"이라는 해석을
고칠 필요가 없습니다. 이 가능성을 바에 적어 두지 않았다면 그냥 첫째 줄로 읽고 넘어갔을
것입니다 — ADR-058을 쓴 사람이 나이기 때문입니다.

고장은 **한 가족에 그대로 모여 있습니다.**

| 가족 | n | OpenAI | Claude |
|---|---:|---:|---:|
| `exact-title` | 40 | 1.0000 | <!--n:reports/judge_holdout_claude.json#summary.by_family.exact-title.content_accuracy-->0.5250<!--/n--> |
| `author`·`citing`·`coauthor`·`reference`·`topcited` | 각 8 | 1.0000 | 1.0000 |

19건 전부 미탐이고(거짓 양성 <!--n:reports/judge_holdout_claude.json#summary.content_false_positive-->0<!--/n-->건),
튜닝 집합의 비율(13/21 = 0.619)과 보류 집합의 비율(19/40 = 0.475)이 같은 크기입니다. **같은
고장이 같은 자리에서 재현됩니다.**

### 프롬프트의 어디가 기울어 있는지

`prompts.JUDGE_CONTENT`에 두 문장이 **비대칭으로** 들어 있습니다.

| | 문장 | 뜻 |
|---|---|---|
| A | `requires_content is true only when … or names which paper one of them must be; an exact title of a paper to return is such a constraint.` | 제목이 **돌려줄 논문**을 가리키면 내용 조건 |
| B | `A title or topic that names the other end of a relationship … does not make requires_content true on its own.` | 제목이 **관계의 상대편**을 가리키면 그래프 조건 |

A는 긴 문장의 종속절에 묻혀 있고 B는 독립된 규칙으로 서 있습니다. `gpt-6-luna`는 둘을 나눠
쓰고, `claude-haiku-5-5`는 **B를 양쪽에 적용합니다.** 그래서 `Find the paper titled: X`에
`requires_content=False`가 나옵니다 — 제목이 요청 전체인데도요.

### 프롬프트를 고치는 값은 공짜가 아닙니다

`JUDGE_CONTENT`의 본문은 기준선에 지문으로 못박혀 있고(`prompt_versions.JUDGE_CONTENT` =
`c09bb82b3769`), 게이트의 `prompt_version` 규칙이 **다른 본문으로 잰 점수를 막습니다** —
설계상 그렇습니다([ADR-017](ADR-017-regression-gate.md)). 그러므로 프롬프트를 채택하면
**기준선을 다시 재야 합니다.**

그래서 탐색 중에는 `prompts.py`를 고치지 않습니다. 후보는 파일로 두고
`judge_study --prompt`로 끼워 넣으며, 보고서에 그 지문을 적습니다. 채택은 네 조건을 넘긴
뒤의 **별개 결정**이고, 기준선 재측정 비용을 함께 적어 판단합니다.

## 결과 2: 후보 프롬프트 v4를 **채택하지 않습니다** (2026-10-22)

A를 B와 **대칭**으로 다시 썼습니다. 제목을 만나면 "질문이 그 제목에게 무엇을 시키는가"로
가르게 하고, 두 경우를 나란히 적었습니다(`edafadf5a830`, 199자 증가). 다른 구간은 손대지
않았습니다.

| | v3 `c09bb82b3769` | v4 `edafadf5a830` |
|---|---:|---:|
| Claude 진단 `requires_content` | 0.7625 | <!--n:reports/judge_holdout_claude_v4.json#summary.content_accuracy-->0.8875<!--/n--> |
| Claude 진단 `exact-title` | 0.5250 | <!--n:reports/judge_holdout_claude_v4.json#summary.by_family.exact-title.content_accuracy-->0.7750<!--/n--> |
| 갈린 쌍 | 19 (p ≈ 0) | <!--n:reports/judge_holdout_claude_v4.json#comparison.requires_content.only_new_wrong-->9<!--/n--> (p = <!--n:reports/judge_holdout_claude_v4.json#comparison.requires_content.sign_test_p-->0.0039<!--/n-->) |
| OpenAI 진단 | 1.0000 | <!--n:reports/judge_holdout_openai_v4.json#summary.content_accuracy-->1.0000<!--/n--> — **판정 변화 <!--n:reports/judge_holdout_openai_v4.json#comparison.both.only_new_wrong-->0<!--/n-->건** |
| OpenAI 기존 61문항 | 1.0000 | <!--n:reports/judge_study_openai_v4.json#summary.content_accuracy-->1.0000<!--/n--> — 회귀 없음 |

격차가 **절반**이 됐습니다(19 → 9). 그런데 미리 적은 네 조건 중 **둘만** 넘깁니다.

| 조건 | 결과 |
|---|---|
| 1. 두 공급자 모두 틀린 비율 ≤ 0.05 | ❌ Claude **0.1125**(9/80) |
| 2. 갈린 쌍 6건 미만 | ❌ **9건**, p = 0.0039 |
| 3. `requires_graph` 두 공급자 모두 유지 | ✅ 양쪽 1.0000, 갈린 쌍 0 |
| 4. 기존 61문항 회귀 없음 | ✅ OpenAI 1.0000 |

**그래서 원래 프롬프트를 둡니다.** 바에 적어 둔 대로 "넷 중 셋만 넘긴 프롬프트를 거의 됐다로
넣지 않습니다" — 여기는 둘입니다.

### 남은 9건은 문구로 닿지 않습니다

9건 **전부** 같은 답입니다 — `requires_content=False`, `requires_graph=False`,
`graph_conditions=[]`. **"이 질문에는 조건이 하나도 없다"** 입니다.

중요한 것은 **무엇이 아닌지**입니다. 제목을 `graph_conditions`로 옮긴 것이 아닙니다(그것이
v2의 오류 방향이었습니다). `requires_graph`는 9건 모두 맞게 `False`입니다. Claude는 제목 조회를
**조건이 아니라 식별**로 봅니다 — "이 논문 하나를 찾아라"는 걸러 내는 일이 아니라는 읽기입니다.

그리고 그 읽기는 **일관됩니다.** 틀린 제목과 맞은 제목의 길이가 같고(중위 10단어), 콜론
비율도 구별되지 않습니다(틀림 6/9, 맞음 17/31). 어려운 제목에서 실패하는 것이 아닙니다.

그래서 이것은 추론 실수가 아니라 **스키마 해석의 차이**입니다. `requires_content`가 두 가지를
한 칸에 담고 있습니다.

| 담긴 것 | 뜻 |
|---|---|
| 주제 제약 | 돌려줄 논문들이 **무엇에 관한 것**이어야 한다 |
| 식별 | 돌려줄 논문 하나가 **어느 논문**인지 이름으로 정해졌다 |

우리 라벨은 둘 다 `True`입니다 — 아래 코드가 내용 검사를 돌려야 하기 때문입니다. `gpt-6-luna`는
그 묶음을 받아들이고 `claude-haiku-5-5`는 받아들이지 않습니다. 문장을 더 얹어 한쪽 공급자를
우리 묶음에 밀어 넣는 것은 **고치는 일이 아니라 맞추는 일**입니다.

### 그러면 무엇이 진짜 고침인가 — 그리고 왜 지금 하지 않는가

칸을 **나누는 것**입니다: `requires_content`를 `names_a_specific_paper`와
`constrains_topic`으로. 그러면 두 공급자가 같은 것을 묻게 됩니다.

지금 하지 않습니다.

- `QuestionVerdict` 스키마, `judge_content`, 그것을 읽는 `candidate_validation`과
  `chatbot`의 분기, `judge_study`의 라벨과 혼동 행렬, `judge_bias`의 비교 모양이 모두
  바뀝니다. **측정 하나가 아니라 설계 변경**입니다.
- 그리고 **지금 그것을 살 이유가 없습니다.** 우리는 OpenAI로 돌고, 그 공급자는 측정한
  141문항(튜닝 61 + 진단 80)에서 **141/141**입니다. 공급자 이식성은 지금 제품 요구가
  아닙니다.
- 사는 이유가 생기는 조건을 적어 둡니다 — **심판을 다른 공급자로 옮겨야 할 때**, 또는
  `gpt-6-luna`가 이 자리에서 떨어질 때입니다. 그때는 문구가 아니라 칸을 나눕니다.

### v4를 채택하면 드는 값

참고로 적어 둡니다. v4는 **OpenAI의 판정을 하나도 바꾸지 않습니다**(141문항에서 변화 0건).
그런데 본문이 바뀌므로 게이트의 `prompt_version` 규칙이 막고, **기준선을 다시 재야 합니다.**
즉 "앱 동작은 그대로인데 기준선 재측정 비용은 전액"입니다. 쓰지 않는 공급자에서의 0.7625 →
0.8875를 그 값으로 사지 않습니다.

### 최종 확인 조각은 열지 않았습니다

40문항이 **그대로 남아 있습니다.** 채택할 프롬프트가 없으므로 확인할 것이 없고, 열면 그 조각도
튜닝 집합이 됩니다. 다음에 칸을 나누게 되면 그때 이 조각이 쓰입니다 — 그것이 이 조각을 남겨 둔
이유입니다.

## 민감도

**바뀌는 조건.**

- 생성은 그래프를 읽습니다. 그래프가 바뀌면 다른 질문이 나옵니다. 그래서 **생성 결과를 파일로
  커밋**하고 측정은 그 파일만 씁니다 — 재현이 그래프 상태에 묶이지 않게.
- 표본 추출에 씨앗을 고정합니다. 고정하지 않으면 "다시 돌려 보니 괜찮았다"가 가능해집니다.
- `exact-title`의 제목은 **그래프에 있는 논문**의 제목입니다. 심판에게 붙이는 논문도 그래프에서
  옵니다. 두 공급자를 **같은 날 연달아** 돌려야 같은 입력입니다(ADR-058과 같은 이유).

**가정.** 템플릿을 그대로 쓰면 "같은 종류의 질문"이라고 가정합니다. 제목의 길이·분야·표기가
튜닝 집합과 다르면 그것만으로 정확도가 움직일 수 있고, 이 설계는 그 차이를 분리하지 못합니다.
`exact-title`에서는 **그 가정을 일부 확인했습니다** — 틀린 제목과 맞은 제목의 길이가 같고(중위
10단어) 콜론 비율도 구별되지 않습니다. 그러나 분야 쏠림은 재지 않았습니다.

**말하지 않는 것.**

- `topic` 가족. 생성하지 않습니다(위).
- 정답 `expected_ids`. 이 집합에는 없습니다 — 심판 판정만 잽니다.
- 어느 프롬프트가 "가장 좋은" 프롬프트인가. 네 조건을 넘기는 것을 찾으면 멈춥니다. 탐색을
  계속하면 최종 확인 조각이 소진됩니다. **v4가 둘만 넘겼으므로 거기서 멈췄고, 최종 조각은
  열지 않았습니다**(40문항 그대로 남아 있습니다).
- 칸을 나눈 스키마(`names_a_specific_paper` + `constrains_topic`)가 실제로 두 공급자를
  일치시키는가. **재지 않았습니다** — 설계 변경이고, 지금 살 이유가 없습니다(위). 사는 이유가
  생기는 조건만 적어 뒀습니다.
- 변동폭. 한 번 돌립니다. ADR-021이 다섯 번 돌려 배운 것이 "변동폭은 표본을 늘릴 때마다
  갱신되는 추정"이라는 것이고, 여기서도 같습니다.

## 재현

```bash
# 보류 질문을 생성해 파일로 커밋합니다(그래프를 읽습니다)
uv run python -m evals.holdout_questions --output evals/judge_holdout.json
```

```bash
# 진단 조각에서 두 공급자를 연달아
uv run --group dev python -m evals.judge_study --holdout evals/judge_holdout.json \
  --split diagnostic --label openai-holdout --output reports/judge_holdout_openai.json
uv run --group dev python -m evals.judge_study --holdout evals/judge_holdout.json \
  --split diagnostic --provider anthropic --label claude-holdout \
  --compare-to reports/judge_holdout_openai.json \
  --output reports/judge_holdout_claude.json
```

## 참고

- [ADR-058](ADR-058-the-independent-judge-only-means-something-with-labels.md)이 이 공백을
  적었습니다 · [ADR-055](ADR-055-the-stable-case-rule-is-not-resolved.md)의 허용 한계 0.20 ·
  [ADR-021](ADR-021-ragas-four-of-six.md)의 판정 모델 기록 규칙
- [`evals/holdout_questions.py`](../../evals/holdout_questions.py) ·
  [`evals/judge_study.py`](../../evals/judge_study.py)
