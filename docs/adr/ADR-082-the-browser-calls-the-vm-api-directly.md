# ADR-082: Next.js 화면은 Vercel에, 브라우저가 VM의 API를 직접 부른다

- **상태:** 채택
- **날짜:** 2026-10-27

## 맥락

이전 계획 4단계입니다([MIGRATION_PLAN](../MIGRATION_PLAN.md)). [시안](https://claude.ai/artifact/38K4WuVFbroXpW8xwB5i5w)의
다섯 화면을 Next.js로 만들어 Vercel 무료 플랜에 올립니다. API는 VM의 FastAPI 층입니다
([ADR-081](ADR-081-fastapi-beside-streamlit-on-the-vm.md)).

화면이 API를 부르는 길은 둘입니다.

1. **Vercel을 거칩니다.** Next.js의 rewrite나 Route Handler가 VM으로 넘깁니다. 같은 출처라 CORS가 없습니다.
   그런데 VM이 보는 IP가 Vercel 서버의 것이 됩니다. ADR-081의 IP별 상한(분당 3·하루 30)이 모든 사람을
   한 덩어리로 세게 되고, 한 사람이 다른 모든 사람의 몫을 씁니다. SSE도 Vercel 함수를 한 번 더 거칩니다.
2. **브라우저가 VM을 직접 부릅니다.** CORS를 열어야 합니다. IP별 상한은 그대로 사람마다 돕니다.

## 결정

- **브라우저가 VM을 직접 부릅니다.** 질문(SSE), 경로 판정, 번역, 부분 그래프가 그렇습니다. 머리줄의
  통계·커밋처럼 상한이 없는 읽기만 Next.js 서버가 부르고 10분 캐시합니다.
- **CORS는 목록과 정규식 둘입니다.** 운영 주소(`arxiv-graphrag.vercel.app`)와 로컬 개발(`localhost:3000`)은
  `API_ALLOWED_ORIGINS`, 브랜치마다 바뀌는 미리보기 주소는 `API_ALLOWED_ORIGIN_REGEX`
  (`arxiv-graphrag-…-rndigkwks-projects.vercel.app`만). 값은 `scripts/make_vm_env.py`가 씁니다.
- **SSE는 `fetch` 스트림으로 읽습니다.** 브라우저의 `EventSource`는 GET만 보냅니다. 질문은 POST입니다.
- **답은 마크다운으로 그립니다.** HTML은 그리지 않습니다(모델이 쓴 글이 태그를 넣지 못합니다).
- **근거 논문의 제목은 답이 아니라 그래프에서 읽습니다**(`/api/subgraph`). 답의 표와 나란히 둡니다.
- **휴대폰 화면은 같은 페이지의 좁은 배치입니다.** 720px 아래에서 경로·근거는 "근거 보기"로 엽니다.
- **화면은 만든 것만 메뉴에 올립니다.** 논문·탐색·품질은 다음 PR들에서 더합니다.

## 민감도

- **CORS는 상한을 대신하지 않습니다.** CORS는 브라우저만 지킵니다. 스크립트는 출처 없이 부릅니다 —
  그것을 막는 것은 여전히 ADR-081의 상한입니다.
- **미리보기 정규식은 팀 이름(`rndigkwks-projects`)에 묶여 있습니다.** 프로젝트를 다른 팀으로 옮기면
  미리보기에서 질문이 CORS로 막힙니다. 정규식을 바꿉니다.
- **VM의 주소가 바뀌면 화면이 API를 잃습니다.** 기본값은 `lib/api.ts`에 있고, Vercel 환경변수
  `NEXT_PUBLIC_API_BASE`로 바꿉니다(빌드 때 들어가므로 바꾼 뒤 다시 빌드합니다).
- **시안의 "단계별 걸린 시간"은 아직 없습니다.** 응답에 단계별 시간이 없어서 전체 시간만 보여 줍니다.
  "함께 인용됨" 점선도 어느 근거와 이어지는지 응답에 없어서 그리지 않습니다.

## 참고

- `web/`, `src/api.py`(CORS), `scripts/make_vm_env.py`, `.github/workflows/ci.yml`의 `web` 작업
