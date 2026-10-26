# ADR-079: VM은 저장소를 마운트하고 HTTPS 하나만 연다 — 스키마는 Aura에서 읽어 옮기고, 벡터 양자화만 같게 못 한다

- **상태:** 채택
- **날짜:** 2026-10-26

## 맥락

[이전 계획](../MIGRATION_PLAN.md) 1단계는 VM 한 대에 Neo4j와 지금 Streamlit 앱을 올리는 것입니다
([ADR-071](ADR-071-double-the-graph-move-only-the-database.md)). GCP 가입 전에 로컬 Docker에서 같은
compose로 리허설했고, 세 가지가 계획과 달랐습니다.

1. **옮기는 스크립트가 스키마를 반만 만들었습니다.** `local_graph_test.py import`는 로컬 실험용
   이름(`local_paper_id`)으로 인덱스 셋과 벡터 인덱스만 만들고, 앱이 제목·저자를 찾는 전문 검색
   인덱스 둘(`arxiv_paper_title_fulltext`, `arxiv_author_name_fulltext`)은 만들지 않습니다. 그대로
   옮기면 앱은 뜨지만 제목 해석이 `CONTAINS`로 떨어집니다([ADR-024](ADR-024-route-by-question-type.md)).
2. **10-14 스냅샷은 낡았습니다.** 논문 37,854편이고 지금 Aura는
   <!--n:reports/vm_rehearsal.json#snapshot.counts.papers-->38665<!--/n-->편입니다. 주간 수집이 계속 씁니다.
3. **Aura의 벡터 인덱스 설정 하나를 Community가 받지 않습니다.** Aura는
   `vector.quantization.type: BINARY`이고, Neo4j 5.26 Community는 그 키를 거절합니다(받는 것은
   `dimensions`·`similarity_function`·`hnsw.m`·`hnsw.ef_construction`·`quantization.enabled`).

## 결정

- **`scripts/move_graph.py`가 스키마를 Aura에서 읽어 같은 이름·같은 옵션으로 다시 만듭니다.**
  `export`가 `SHOW INDEXES`·`SHOW CONSTRAINTS`와 논문 속성의 값 타입을 스냅샷에 적고, `load`가
  그대로 만들고, `verify`가 비교합니다. 옮기지 않는 라벨·관계가 Aura에 생기면 `export`가 멈춥니다.
- **벡터 인덱스는 Community가 받는 넷만 옮기고, 버린 것을 `load_report.json`에 적습니다.** 양자화는
  Community 기본값(켜짐)입니다. 버린 것 때문에 검색이 달라지는지는 재서 아래에 적습니다.
- **`load`는 빈 DB에만, Aura가 아닌 곳에만 씁니다.** 지우는 명령은 담지 않습니다.
- **앱 컨테이너는 의존성만 굽고, 코드는 VM의 clone을 마운트합니다.** 배포가 `git pull` + 재시작이
  되고, `src/release.py`가 `.git`을 읽어 커밋을 찍는 장치(배포 점검이 읽습니다,
  [ADR-072](ADR-072-check-the-deployed-app-in-a-browser.md))가 그대로 동작합니다. 리허설에서 배포 점검이 화면·모듈 둘 다 같은 커밋으로 통과했습니다.
- **인터넷에는 Caddy의 80·443만 엽니다.** 도메인은 고정 IP를 sslip.io에 붙여 쓰고 인증서는 Caddy가
  받습니다. Neo4j·앱은 호스트 포트가 없습니다(`tests/test_move_graph.py`).
- **Neo4j 설정은 ADR-071을 잰 것과 같습니다**(같은 이미지 digest, heap 1G, page cache 1G). 테스트가
  `compose.local.yml`과 대조합니다.
- **VM의 `.env`에는 새 Neo4j 비밀번호와 OpenAI 키만 둡니다.** Aura 비밀번호와 GDS 세션 키는 두지
  않고, Langfuse는 꺼 둡니다(`scripts/make_vm_env.py`).

## 리허설 결과 (로컬 Docker, 2026-10-26)

`reports/vm_rehearsal.json`입니다. **GCP VM이 아닙니다** — CPU·디스크·네트워크가 다릅니다.

- 내보내기 <!--n:reports/vm_rehearsal.json#snapshot.export_seconds-->303.4<!--/n-->초(읽기 전용),
  싣기 <!--n:reports/vm_rehearsal.json#load.elapsed_seconds-->203.7<!--/n-->초.
  `verify`의 문제 0건. 전문 검색 인덱스 하나를 지우고 돌리면 `인덱스 없음`으로 실패합니다(음성 대조).
- **검색.** 같은 31사례를 `evals.run --mode retrieval --top-k 10`으로 양쪽에서 돌렸습니다.
  - 순위 지표는 같습니다. hit@k <!--n:reports/vm_rehearsal.json#retrieval.summary.hit_at_k.after-->1.0<!--/n-->,
    recall@k <!--n:reports/vm_rehearsal.json#retrieval.summary.recall_at_k.before-->0.6837<!--/n--> →
    <!--n:reports/vm_rehearsal.json#retrieval.summary.recall_at_k.after-->0.6836<!--/n-->, MRR
    <!--n:reports/vm_rehearsal.json#retrieval.summary.reciprocal_rank.after-->1.0<!--/n-->.
  - 목록은 다릅니다. 31건 중 <!--n:reports/vm_rehearsal.json#retrieval.identical_lists-->3<!--/n-->건만
    열 개가 똑같고, 1위는 <!--n:reports/vm_rehearsal.json#retrieval.same_top1-->30<!--/n-->건이 같고,
    평균 겹침은 <!--n:reports/vm_rehearsal.json#retrieval.mean_overlap_at_k-->0.894<!--/n-->입니다.
  - **잡음이 아닙니다.** 같은 쪽을 두 번 돌리면 양쪽 다 31/31 똑같았습니다. 인덱스가 다른 것이고,
    다른 것은 양자화뿐입니다.
- **메모리.** 질문 하나를 처리하는 동안 docker stats 피크가 Neo4j
  <!--n:reports/vm_rehearsal.json#memory_peak_mib.neo4j-->2321.4<!--/n-->MiB, 앱
  <!--n:reports/vm_rehearsal.json#memory_peak_mib.app-->340.4<!--/n-->MiB, Caddy
  <!--n:reports/vm_rehearsal.json#memory_peak_mib.caddy-->34.8<!--/n-->MiB, 합
  <!--n:reports/vm_rehearsal.json#memory_peak_mib.total-->2696.6<!--/n-->MiB입니다. 4GB VM에 1GB 남짓
  남는다는 ADR-071의 추정과 맞지만, 질문 하나·16표본입니다.
- 저자·주제(벡터)·인용 질문 셋이 예외 없이 답했습니다.

## GCP VM 첫날 (2026-10-26)

`reports/vm_day1.json`입니다. e2-medium(4GB), 서울, 주소 `https://8-230-25-138.sslip.io`.

- 싣기와 검증이 <!--n:reports/vm_day1.json#load_wall_seconds-->546.5<!--/n-->초(리허설의 약 2.7배 - 디스크와 CPU가 느립니다), 검증 문제 0건.
  Let's Encrypt 인증서는 Caddy가 첫 시도에 받았습니다.
- 배포 점검이 첫 화면 <!--n:reports/vm_day1.json#deploy_check.first_screen_seconds-->25<!--/n-->초, 화면·모듈 커밋 일치로 통과했습니다.
- **검색은 리허설과 똑같습니다.** 1위 <!--n:reports/vm_day1.json#retrieval.same_top1-->30<!--/n-->/31, 겹침 <!--n:reports/vm_day1.json#retrieval.mean_overlap_at_k-->0.894<!--/n-->,
  recall@k <!--n:reports/vm_day1.json#retrieval.summary.recall_at_k.after-->0.6836<!--/n-->.
- **메모리.** 질문 셋 동안 Neo4j <!--n:reports/vm_day1.json#memory_peak_mib.neo4j-->1609.7<!--/n-->MiB, 앱 <!--n:reports/vm_day1.json#memory_peak_mib.app-->409.8<!--/n-->MiB,
  호스트 사용 <!--n:reports/vm_day1.json#host_used_peak_mib-->2673<!--/n-->MiB, 그 뒤 가용 <!--n:reports/vm_day1.json#host_available_after_mib-->1280<!--/n-->MiB.
  스왑 <!--n:reports/vm_day1.json#swap_used_mib.after-->342<!--/n-->MiB는 **싣는 동안 밀려난 것**입니다 - 질문 동안 늘지 않았고 그 뒤
  swap-in/out이 0입니다. 그래서 4GB를 유지합니다.

## 민감도

- **목록 겹침 0.894가 품질에 무엇을 하는지는 모릅니다.** 31사례의 순위 지표는 같았지만 정답 표지가
  있는 논문만 봅니다. 답변 품질(유료 평가)로 재지 않았습니다. 지표가 떨어지는 사례가 나오면 먼저 볼
  것은 양자화입니다 — `vector.quantization.enabled: false`로 다시 만들고 다시 잽니다.
- **메모리는 질문 하나의 값입니다.** 동시 사용자가 늘면 앱이 먼저 커집니다. setup.sh가 스왑 2GB를
  만들어 OOM 대신 느려지게 했고, **질문을 받는 동안** 스왑 사용이 늘면(swap-in이 0이 아니게 되면) 8GB로 올립니다. 싣기·빌드가 밀어낸 스왑은 세지 않습니다(ADR-071 결정 4는 VM에서 다시 잽니다).
- **sslip.io는 남의 DNS입니다.** 멈추면 주소가 죽습니다. 그때는 도메인을 사서 `APP_DOMAIN`만 바꿉니다.
- **주간 수집은 아직 Aura에 씁니다.** 1단계의 VM은 옮긴 날의 사본입니다. 2단계에서 수집을 VM으로
  옮길 때 한 번 더 내보내 싣습니다.

## 참고

- `scripts/move_graph.py`, `scripts/make_vm_env.py`, `deploy/vm/`, `tests/test_move_graph.py`
- [GCP VM 실행 안내](../GCP_VM.md)
