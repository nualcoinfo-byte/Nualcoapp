# syntax=docker/dockerfile:1
FROM python:3.12-slim

WORKDIR /app

ENV STREAMLIT_SERVER_HEADLESS=true
ENV STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
ENV PYTHONUNBUFFERED=1

# Only requirements.txt is copied before the install step, so this layer stays
# cached across every push that doesn't touch dependencies (i.e. almost every
# push to app_pages/*.py).
#
# No BuildKit cache mount on purpose: Railway only accepts cache mount ids of
# the form `s/<service id>-<path>` with no variable substitution, so a
# hardcoded id ties the Dockerfile to a single service and fails the build for
# every other one (e.g. the staging environment).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py database.py pages_common.py ./
COPY app_pages ./app_pages
COPY assets ./assets
COPY .streamlit ./.streamlit

EXPOSE 8501

CMD ["sh", "-c", "streamlit run app.py --server.address 0.0.0.0 --server.port ${PORT:-8501} --server.headless true --server.fileWatcherType none --browser.gatherUsageStats false"]
