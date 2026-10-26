# 앱 컨테이너는 **의존성만** 굽습니다. 코드는 VM에 clone한 저장소를 /app에 마운트합니다 -
# 배포가 `git pull` + 재시작이 되고, `src/release.py`가 `.git`을 읽어 화면에 커밋을 찍습니다.
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.29 /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy PATH=/opt/venv/bin:$PATH
WORKDIR /build
COPY pyproject.toml uv.lock .python-version ./
# dev 그룹(평가 전용)은 넣지 않습니다 - Streamlit Cloud와 같은 설치입니다(pyproject의 주석).
RUN uv sync --locked --no-dev --no-install-project
WORKDIR /app
EXPOSE 8501
# 파일 감시는 끕니다. 배포는 `git pull` 뒤 재시작이고, 감시는 VM CPU를 계속 씁니다.
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501", \
     "--server.headless=true", "--browser.gatherUsageStats=false", \
     "--server.fileWatcherType=none"]
