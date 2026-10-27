# API 컨테이너는 **의존성만** 굽습니다. 코드는 VM에 clone한 저장소를 /app에 마운트합니다 -
# 배포가 `git pull` + 재시작이 되고, `src/release.py`가 `.git`을 읽어 /api/health에 커밋을 찍습니다.
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.29 /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy PATH=/opt/venv/bin:$PATH
WORKDIR /build
COPY pyproject.toml uv.lock .python-version ./
# dev 그룹(평가 전용)은 넣지 않습니다 - Streamlit Cloud와 같은 설치에 FastAPI 층(`api` extra,
# ADR-081)만 더합니다. VM의 Streamlit은 내렸습니다(ADR-083) - 화면은 Vercel입니다.
RUN uv sync --locked --no-dev --no-install-project --extra api
WORKDIR /app
EXPOSE 8000
# 워커 하나: 상한(Limiter)이 프로세스 메모리에 있고, 인용망도 프로세스마다 한 벌씩 올라갑니다.
# --reload는 켜지 않습니다. 배포는 `git pull` 뒤 재시작이고, 파일 감시는 VM CPU를 계속 씁니다.
CMD ["uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8000"]
