FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
    LLM_PROVIDER=local \
    TRACERECOVER_ENABLE_DOCS=false

WORKDIR /app

# Install dependencies first for layer caching
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

# Copy application source, datasets, and baseline benchmark artifacts
COPY app.py config.py report_generator.py web_dashboard.py ./
COPY agent/ ./agent/
COPY grounding/ ./grounding/
COPY healing/ ./healing/
COPY uncertainty/ ./uncertainty/
COPY tools/ ./tools/
COPY trace/ ./trace/
COPY eval/ ./eval/
COPY data/ ./data/
COPY static/ ./static/
COPY results/ ./results/

# Prepare writable non-root runtime directories
RUN useradd --create-home --shell /bin/bash tracerecover \
    && mkdir -p /app/logs /var/data/results /var/data/logs /var/data/versions /var/data/conversations /var/data/unseen_benchmarks \
    && chown -R tracerecover:tracerecover /app /var/data

USER tracerecover

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import os, urllib.request; p = os.getenv('PORT', '8000'); urllib.request.urlopen(f'http://127.0.0.1:{p}/api/health', timeout=3)" || exit 1

CMD ["sh", "-c", "exec uvicorn web_dashboard:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers"]
