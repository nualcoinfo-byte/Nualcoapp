# syntax=docker/dockerfile:1
FROM python:3.12-slim

WORKDIR /app

ENV STREAMLIT_SERVER_HEADLESS=true
ENV STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
ENV PYTHONUNBUFFERED=1

# Only requirements.txt is copied before the install step, so this layer -
# and pip's download/build cache below - stays warm across every push that
# doesn't touch dependencies (i.e. almost every push to app_pages/*.py).
#
# Railway's builder requires cache mount ids in the form
# `s/<service id>-<path>` (no variable substitution allowed here, so the
# service id is hardcoded) - see https://docs.railway.com/builds/dockerfiles.
# Service id is nualco (production): f09da96b-bae2-4384-89ff-2febcba176cb.
COPY requirements.txt .
RUN --mount=type=cache,id=s/f09da96b-bae2-4384-89ff-2febcba176cb-root-cache-pip,target=/root/.cache/pip \
    pip install -r requirements.txt

COPY app.py database.py neon_http.py pages_common.py ./
COPY app_pages ./app_pages
COPY assets ./assets
COPY .streamlit ./.streamlit

EXPOSE 8501

CMD ["sh", "-c", "streamlit run app.py --server.address 0.0.0.0 --server.port ${PORT:-8501} --server.headless true --server.fileWatcherType none --browser.gatherUsageStats false"]
