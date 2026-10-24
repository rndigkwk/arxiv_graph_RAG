# ADR-068: 모듈은 `src/`에 산다 — 손으로 적은 목록을 지우는 것이 목적이다

- **상태:** 채택
- **날짜:** 2026-10-25

## 맥락

루트에 `.py` 파일이 **19개** 있었습니다. 같은 사람이 쓰는 다른 저장소(`dograg`)는 이미
`main.py`만 루트에 두고 모듈 48개를 `src/`에 넣고 있어서, 둘을 나란히 보는 사람에게
**같은 프로젝트가 두 가지 규칙으로** 보였습니다.

그런데 이 ADR을 쓰게 만든 이유는 모양이 아닙니다. CI의 lint 줄이 **루트 모듈 14개를 손으로**
적고 있었습니다.

```
ruff check answer_validation.py candidate_validation.py cypher_validation.py prompts.py
  agent_graph.py tool_timing.py warmup.py routing.py abstain.py pagerank.py title_search.py
  forecast.py quality_board.py citations.py evals tests scripts pipeline models
```

**이 저장소에서 손으로 유지하는 목록이 조용히 갈라진 것이 일곱 번입니다**(`DERIVED_KEYS`,
문서 검사기 대상, `compileall` 모듈 목록, ADR-032 경로 목록, 용량 한도, 민감도 절, ADR 색인
표). 그리고 **이 목록도 이미 갈라져 있었습니다** — `chatbot.py`(1,255줄)와 `app.py`(1,318줄),
`metagraph.py`, `gds_pagerank.py`, `paper_library.py`가 **한 번도 lint된 적이 없습니다.**
어제 `warmup.py`를 더할 때 저 줄에 손으로 한 낱말을 적었는데, 그것이 이 목록의 작동 방식이
문제라는 증거였습니다.

## 결정

**`app.py`만 루트에 남기고 모듈 18개를 `src/`로 옮깁니다.**

1. CI의 lint 줄이 **`ruff check src app.py evals tests scripts pipeline models`**가 됩니다.
   손 목록이 사라졌고, 앞으로 `src/`에 파일을 더하면 **적지 않아도 검사됩니다.**
2. `app.py`는 **옮기지 않습니다.** Streamlit Community Cloud의 "main file" 설정이 그 경로를
   가리키고 있어, 옮기면 그 설정을 바꾸기 전까지 배포가 깨집니다. 저장소 안에서 고칠 수 없는
   것에 기대는 변경은 하지 않습니다. `dograg`의 `main.py`도 같은 자리에 있습니다.
3. `src/`에 `__init__.py`를 두지 않습니다(PEP 420 namespace package). `dograg`가 같은 방식이고,
   CI가 `python -m pytest`로 돌려 저장소 뿌리가 `sys.path`에 들어가므로 `from src import x`가
   그대로 됩니다. **설정 파일을 새로 만들지 않았습니다.**
4. **화면 분리(`app_pages/`)는 하지 않았습니다.** 1,318줄을 탭별로 가르는 것은 이동이 아니라
   리팩터링이고, 섞으면 "동작이 바뀌지 않았다"는 이 PR의 보증이 사라집니다.

## 무엇이 드러났는가 — 긴 줄 18개

`app.py`와 `chatbot.py`가 처음 lint에 들어오자 **E501이 18건** 나왔습니다(앞의 12, 뒤의 6).
전부 전에도 있었고 아무도 보지 않았습니다. 그중 **넷은 접지 않았습니다.**

| 줄 | 왜 접지 않는가 | 한 일 |
|---|---|---|
| `chatbot.py`의 스키마 설명 2줄 | **모델이 읽는 글**입니다. 줄바꿈을 넣으면 Text2Cypher가 보는 글이 바뀝니다 | `# noqa: E501` |
| 비교 시스템 프롬프트 1줄 | 같은 이유 | `# noqa: E501` |
| `app.py`의 문서 링크 1줄 | URL은 쪼갤 수 없습니다. 나누면 화면의 링크가 깨집니다 | `# noqa: E501` |

나머지 14줄은 **화면 문자열**이라 암시적 연결로 접었습니다 — 이어 붙인 글자는 같습니다.
이 구분은 새로 만든 규칙이 아닙니다. `pyproject.toml`이 이미 `prompts.py`를 E501에서 빼 두고
*"프롬프트 본문은 코드가 아니라 데이터입니다"*라고 적어 두었고, 그 키를 `src/prompts.py`로
옮겼습니다. 모듈 전체를 면제하지 않고 **네 줄만** 면제한 이유는, `chatbot.py`의 나머지
1,255줄은 코드이기 때문입니다.

## 민감도

**바뀌는 조건.**

- **Streamlit Cloud의 main file 설정이 바뀌면** `app.py`를 루트에 두는 이유가 사라집니다.
  그때는 `src/app.py`로 옮기고 루트에 한 줄짜리 진입점을 두는 쪽이 더 깔끔합니다.
- **`src/`를 설치 가능한 패키지로 만들면** namespace package로 두는 선택을 다시 봐야 합니다.
  지금은 아무것도 `pip install`하지 않으므로 `__init__.py`가 사는 값이 없습니다.
- **pytest를 `python -m pytest`가 아니라 `pytest`로 부르면** 저장소 뿌리가 `sys.path`에 들어가지
  않아 `from src import x`가 깨집니다. CI와 문서가 모두 `python -m pytest`를 쓰고, 그 형태가
  [ADR-044](ADR-044-not-run-is-not-a-regression.md)의 기록 방식과도 같습니다.
- **동작이 바뀌지 않았다는 주장의 근거는 테스트뿐입니다.** 1,053건이 이동 전과 **같은 수로**
  통과합니다. 이동이 깨뜨리는 것은 거의 전부 임포트 오류라 조용히 지나가지 않습니다 —
  실제로 `importlib.import_module("chatbot")` 한 곳이 문자열이어서 정규식에 안 걸렸고,
  테스트 17건이 그것을 잡았습니다.

**말하지 않는 것.**

- **산문은 그대로입니다.** 문서가 `chatbot.py`라고 부르는 자리는 수백 곳이고, 링크(37곳)만
  `src/`로 옮겼습니다. ADR은 그때의 기록이라 소급해 고치지 않습니다 — `docs/adr/README.md`가
  적은 "뒤집힌 결정을 지우지 않습니다"와 같은 선입니다.
- **`git log --follow` 없이는 이력이 끊겨 보입니다.** `git mv`로 옮겨 Git은 추적하지만, 웹에서
  파일을 눌러 보는 사람에게는 새 파일처럼 보입니다. 이것이 이 변경의 유일한 실질적 손해입니다.
- **아무것도 빠르거나 좋아지지 않습니다.** 동작 변화 0입니다. 사는 것은 "다음에 모듈을 더할 때
  목록을 안 적어도 된다"뿐이고, 그것 하나를 위해 **40개 파일을 건드렸습니다.**
- **`app_pages/` 분리의 값.** 재지 않았습니다. `app.py`는 여전히 1,318줄입니다.

## 참고

- [ADR-032](ADR-032-close-the-model-lifecycle.md)·[ADR-034](ADR-034-measure-every-eligible-seed.md)가
  손으로 적은 목록이 갈라진 앞선 자리를 적습니다 ·
  [ADR-016](ADR-016-stay-on-streamlit.md)이 배포가 Streamlit Cloud에 묶여 있다고 적습니다 ·
  [ADR-067](ADR-067-warm-the-citation-graph-at-boot.md)의 `warmup.py`가 저 손 목록에 더해진
  마지막 줄이었습니다
