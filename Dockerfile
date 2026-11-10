FROM python:3.12-slim

# Small, no root: the server runs as an ordinary user and only writes to /data.
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 GYMLOG_DATA_DIR=/data
RUN useradd --create-home --uid 10001 gymlog && mkdir /data && chown gymlog /data
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
USER gymlog
VOLUME /data
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4).status == 200 else 1)"
CMD ["uvicorn", "app.main:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8080"]
