FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py database.py neon_http.py ./
COPY assets ./assets
COPY .streamlit ./.streamlit

ENV STREAMLIT_SERVER_HEADLESS=true
ENV STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
ENV PYTHONUNBUFFERED=1

EXPOSE 8501

CMD ["sh", "-c", "streamlit run app.py --server.address 0.0.0.0 --server.port ${PORT:-8501} --server.headless true --server.fileWatcherType none --browser.gatherUsageStats false"]
