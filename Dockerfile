# syntax=docker/dockerfile:1
FROM python:3.12-slim

WORKDIR /app

ENV STREAMLIT_SERVER_HEADLESS=true
ENV STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
ENV PYTHONUNBUFFERED=1

# Only requirements.txt is copied before the install step, so this layer -
# and pip's download/build cache below - stays warm across every push that
# doesn't touch dependencies (i.e. almost every push to app_pages/*.py).
COPY requirements.txt .
RUN --mount=type=cache,id=pip-cache,target=/root/.cache/pip \
    pip install -r requirements.txt

COPY app.py database.py neon_http.py pages_common.py ./
COPY app_pages ./app_pages
COPY assets ./assets
COPY .streamlit ./.streamlit

EXPOSE 8501

CMD ["sh", "-c", "streamlit run app.py --server.address 0.0.0.0 --server.port ${PORT:-8501} --server.headless true --server.fileWatcherType none --browser.gatherUsageStats false"]
