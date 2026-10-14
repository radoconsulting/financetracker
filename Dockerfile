FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MEERWAARDE_DATA=/data

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir ".[app]" && useradd --create-home --uid 1000 app && mkdir -p /data && chown app /data

USER app
VOLUME ["/data"]
EXPOSE 8501
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')" || exit 1
CMD ["meerwaarde", "app", "--host", "0.0.0.0", "--port", "8501", "--data-dir", "/data"]
