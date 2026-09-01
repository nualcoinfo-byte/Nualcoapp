FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py database.py neon_http.py ./
COPY assets ./assets
# Only config.toml. secrets.toml is excluded in .dockerignore so the database
# credential is never baked into an image layer; supply DATABASE_URL at runtime.
COPY .streamlit/config.toml ./.streamlit/config.toml

ENV STREAMLIT_SERVER_HEADLESS=true
ENV STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
ENV PYTHONUNBUFFERED=1

EXPOSE 8501

CMD ["sh", "-c", "streamlit run app.py --server.address 0.0.0.0 --server.port ${PORT:-8501} --server.headless true --server.fileWatcherType none --browser.gatherUsageStats false"]
