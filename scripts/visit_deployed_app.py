"""배포가 깨어 있고 **새 코드를 돌리는지** 확인합니다.

    python scripts/visit_deployed_app.py --expect-commit <sha>      # VM의 API와 Vercel 화면
    uvx --with playwright==1.64.1 python scripts/visit_deployed_app.py --streamlit [URL]
    # Streamlit 판(백업 앱). 처음 한 번: uvx --with playwright==1.64.1 playwright install chromium

**2026-10-27부터 VM에는 Streamlit이 없습니다**(ADR-083). 화면은 Vercel의 Next.js이고, VM은
API와 DB만 돌립니다. 그래서 기본 점검은 브라우저 없이 `/api/health`의 두 커밋(디스크·검색
모듈)을 읽습니다 - 아래 교훈 2가 그대로 적용됩니다. 그래프가 살아 있는지는 `/api/stats`로,
화면이 떠 있는지는 Vercel 주소의 응답으로 봅니다. 아래 설명은 `--streamlit` 점검의 것입니다.

`keep_awake.py`의 HTTP 방문으로는 두 가지를 못 합니다. 둘 다 이 저장소에서 실제로 일어났습니다.

1. **잠든 앱을 깨우지 못합니다.** Streamlit Community Cloud는 브라우저 세션을 세고, 잠든 앱은
   "Yes, get this app back up!" 버튼을 눌러야 일어납니다. 2026-10-25에 `Zzzz` 화면이 실제로
   보였는데 12시간 방문은 그동안 성공으로 끝나 있었습니다(ADR-016). 이 점검은 그 버튼을 누릅니다.
2. **옛 코드를 알아보지 못합니다.** 재부팅 전까지 `chatbot` 모듈이 옛것으로 남은 일이 두 번
   있었습니다(ADR-072). 화면의 배포 표시(`src/release.py`)를 읽어 기대한 커밋과, 그리고 검색
   모듈이 불러온 커밋과 대조합니다.

**모델을 부르지 않습니다.** 질문을 던지지 않고 화면이 그려지는지만 봅니다(0원). 화면 준비의
신호는 "적재된 논문" 캡션이고, 그 줄은 Aura에 연결된 뒤에만 그려지므로 DB도 함께 확인됩니다.
dograg의 같은 점검(`scripts/check_deployed_app.py`)에서 가져왔습니다.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time

# VM은 API와 DB만 돌립니다(ADR-083). Streamlit 판은 Streamlit Cloud의 백업 앱 하나만 남았습니다.
API_URL = "https://8-230-25-138.sslip.io"
WEB_URL = "https://arxiv-graphrag.vercel.app/"
WEB_READY_TEXT = "arXiv GraphRAG"
APP_URL = "https://arxivgraphrag-nxuoq53irzeuzpqgdtkeun.streamlit.app/"
OLDER = "이전 코드"  # src.release.OLDER와 같은 뜻. 스크립트가 src를 임포트하지 않습니다
WAKE_BUTTON = "Yes, get this app back up!"
READY_TEXT = "적재된 논문"  # app.main의 캡션. DB 연결 뒤에만 그려집니다
DB_FAILED_TEXT = "실시간 그래프 연결을 준비하지 못했습니다"  # app.main의 준비 실패 안내
ERROR_SELECTOR = '[data-testid="stException"]'
ERROR_TEXTS = ("This app has encountered an error", "Traceback (most recent call last)")
STAMP = re.compile(r"배포 ([0-9a-f]{7}|확인 불가)")
STALE = re.compile(r"검색 모듈 ([0-9a-f]{7}|이전 코드)")
READY_TIMEOUT_S = 420  # 잠든 앱이 깨어나고, 인용망 예열과 별개로 첫 화면이 뜨기까지
COMMIT_WAIT_S = 600  # VM 타이머(2분) + 의존성이 바뀌었을 때의 이미지 빌드를 기다립니다
SETTLE_S = 15


def parse_stamp(text: str) -> dict[str, str | None]:
    """화면 글자에서 배포 표시를 읽습니다. 모양은 `src.release.stamp`가 정합니다."""
    app = STAMP.search(text)
    module = STALE.search(text)
    app_commit = app.group(1) if app else None
    if app_commit == "확인 불가":
        app_commit = None
    return {"found": bool(app), "app": app_commit,
            "module": module.group(1) if module else app_commit}


def verdict(expected: str | None, stamp: dict[str, str | None]) -> str | None:
    """문제가 없으면 `None`, 있으면 사람이 읽을 이유 한 줄입니다."""
    if not stamp["found"]:
        return ("화면에 배포 표시가 없습니다 - 이 점검보다 오래된 코드이거나 "
                "표시 모양이 바뀌었습니다")
    if stamp["app"] is None:
        return "배포 커밋을 읽지 못했습니다(.git 없음) - 새 코드인지 확인할 수 없습니다"
    if stamp["module"] != stamp["app"]:
        return (f"검색 모듈이 옛 코드입니다(화면 {stamp['app']}, 모듈 {stamp['module']}). "
                "VM이면 `journalctl -u arxiv-deploy`, Streamlit Cloud면 재부팅하세요")
    if expected and not expected.startswith(stamp["app"]):
        return f"배포가 기대한 커밋이 아닙니다(화면 {stamp['app']}, 기대 {expected[:7]})"
    return None


def still_deploying(expected: str | None, stamp: dict[str, str | None]) -> bool:
    """화면이나 모듈이 아직 기대한 커밋이 아니면 배포가 오는 중일 수 있습니다.

    VM은 저장소를 마운트하므로(ADR-079) `git merge` 순간 화면의 커밋이 먼저 바뀌고, 모듈은
    재시작(이미지를 다시 구우면 몇 분 뒤)에야 바뀝니다. 2026-10-27 #64 배포에서 그 사이를 옛
    모듈로 읽고 실패했습니다 - 화면 커밋만 기다리던 때입니다. 끝내 안 바뀌면 COMMIT_WAIT_S 뒤
    `verdict`가 실패로 셉니다.
    """
    if not expected or not stamp["app"]:
        return False
    return not all(c and expected.startswith(c) for c in (stamp["app"], stamp["module"]))


def api_stamp(health: dict) -> dict[str, str | None]:
    """`/api/health`를 화면 배포 표시와 같은 모양으로. 모듈 커밋이 없으면 옛 모듈입니다."""
    commit = health.get("commit")
    return {"found": "commit" in health, "app": commit,
            "module": health.get("module_commit") or (OLDER if commit else None)}


def read_json(url: str) -> dict:
    import json
    from urllib.request import Request, urlopen

    with urlopen(Request(url, headers={"User-Agent": "deploy-check"}), timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def check_api(base: str, web: str, expected: str | None) -> int:
    """VM의 API가 기대한 커밋을 돌리고 그래프를 읽는지, Vercel 화면이 뜨는지 봅니다(0원)."""
    from urllib.request import Request, urlopen

    started = time.perf_counter()
    while True:
        try:
            stamp = api_stamp(read_json(base + "/api/health"))
            problem = verdict(expected, stamp)
        except OSError as exc:  # 재시작 중이면 잠깐 502입니다
            stamp, problem = {"found": False, "app": None, "module": None}, f"API 응답 없음({exc})"
        waiting = problem and (stamp["app"] is None or still_deploying(expected, stamp))
        if not waiting or time.perf_counter() - started > COMMIT_WAIT_S:
            break
        time.sleep(30)
    if problem:
        raise SystemExit(f"실패: {problem}")
    papers = read_json(base + "/api/stats")["counts"]["papers"]
    if not papers:
        raise SystemExit("실패: API가 그래프에서 논문을 하나도 읽지 못했습니다")
    with urlopen(Request(web, headers={"User-Agent": "deploy-check"}), timeout=60) as response:
        page = response.read().decode("utf-8", "replace")
    if WEB_READY_TEXT not in page:
        raise SystemExit(f"실패: 화면({web})에 {WEB_READY_TEXT!r}가 없습니다")
    waited = time.perf_counter() - started
    summary(f"- API {stamp['app']} · 모듈 {stamp['module']} · 논문 {papers:,}편 · 화면 응답 · "
            f"{waited:.0f}초" + (f" · 기대 {expected[:7]}" if expected else ""))
    return 0


def error_in(texts: list[str], error_boxes: int = 0) -> str | None:
    """화면에 뜬 오류 하나. Streamlit의 예외 상자와 준비 실패 안내를 봅니다."""
    if error_boxes:
        return f"Streamlit 예외 상자 {error_boxes}개"
    joined = "\n".join(texts)
    if DB_FAILED_TEXT in joined:
        return "앱이 그래프 DB에 연결하지 못했습니다(Aura 일시 중지 또는 접속 정보)"
    for marker in ERROR_TEXTS:
        if marker in joined:
            return f"화면에 오류 문구: {marker}"
    return None


def frame_texts(page) -> list[str]:
    """앱은 *.streamlit.app 안의 iframe에서 그려집니다. 모든 프레임의 글자를 모읍니다."""
    texts = []
    for frame in page.frames:
        try:
            texts.append(frame.locator("body").inner_text(timeout=5_000))
        except Exception:  # noqa: BLE001 - 떼어지는 중인 프레임은 건너뜁니다
            continue
    return texts


def error_boxes(page) -> int:
    return sum(frame.locator(ERROR_SELECTOR).count() for frame in page.frames)


def wait_ready(page, started: float) -> str:
    """깨우고, 첫 화면이 뜰 때까지 기다립니다. 돌려주는 값은 상태 한 단어입니다."""
    state = "awake"
    while time.perf_counter() - started < READY_TIMEOUT_S:
        button = page.get_by_role("button", name=WAKE_BUTTON)
        if button.count():
            state = "woken"
            button.first.click()
        texts = frame_texts(page)
        problem = error_in(texts, error_boxes(page))
        if problem:
            raise SystemExit(f"실패: {problem}")
        if any(READY_TEXT in text for text in texts):
            return state
        page.wait_for_timeout(3_000)
    raise SystemExit(f"실패: {READY_TIMEOUT_S}초 안에 첫 화면이 뜨지 않았습니다")


def summary(line: str) -> None:
    print(line)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as out:
            out.write(line + "\n")


def check(url: str, expected: str | None) -> int:
    from playwright.sync_api import sync_playwright

    started = time.perf_counter()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=120_000)
        state = wait_ready(page, started)
        ready = time.perf_counter() - started
        # 탭은 매 실행마다 전부 그려지므로 첫 화면 뒤 조금 기다리면 다른 탭의 예외도 드러납니다.
        page.wait_for_timeout(SETTLE_S * 1_000)
        while True:
            texts = frame_texts(page)
            problem = error_in(texts, error_boxes(page))
            if problem:
                raise SystemExit(f"실패: {problem}")
            stamp = parse_stamp("\n".join(texts))
            problem = verdict(expected, stamp)
            redeploying = problem and still_deploying(expected, stamp)
            if not redeploying or time.perf_counter() - started > COMMIT_WAIT_S:
                break
            # 화면이나 모듈이 아직 옛 커밋이면 재배포가 오는 중일 수 있습니다. 다시 불러 봅니다.
            page.wait_for_timeout(30_000)
            page.reload(wait_until="domcontentloaded")
            wait_ready(page, time.perf_counter())
        browser.close()
    line = (f"- 배포 앱: {state}, 첫 화면 {ready:.0f}초, "
            f"화면 {stamp['app']} · 모듈 {stamp['module']}")
    summary(line + (f" · 기대 {expected[:7]}" if expected else ""))
    if problem:
        raise SystemExit(f"실패: {problem}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="배포가 깨어 있고 새 코드를 돌리는지 봅니다.")
    parser.add_argument("--streamlit", nargs="?", const=APP_URL, metavar="URL",
                        help="API 대신 Streamlit 판을 브라우저로 엽니다(기본은 백업 앱)")
    parser.add_argument("--expect-commit", help="배포돼 있어야 할 커밋(워크플로가 넘깁니다)")
    args = parser.parse_args()
    if args.streamlit:
        return check(args.streamlit, args.expect_commit)
    return check_api(API_URL, WEB_URL, args.expect_commit)


if __name__ == "__main__":
    sys.exit(main())
