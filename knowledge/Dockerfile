FROM python:3.12-slim AS lexical

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MUSEBRIDGE_RETRIEVAL_MODE=lexical \
    HOST=0.0.0.0 \
    PORT=8001

COPY knowledge/*.py ./knowledge/
COPY knowledge/data/aic-seed-v0.1.json ./knowledge/data/aic-seed-v0.1.json

USER 10001:10001
EXPOSE 8001

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python3", "-c", "import os; from urllib.request import ProxyHandler, build_opener; build_opener(ProxyHandler({})).open('http://127.0.0.1:' + os.environ['PORT'] + '/readyz', timeout=2).close()"]

CMD ["python3", "-m", "knowledge.http_service"]

FROM lexical AS hybrid

USER root
COPY knowledge/requirements.txt ./knowledge/requirements.txt
RUN pip install --no-cache-dir -r knowledge/requirements.txt \
    && install -d -m 0700 -o 10001 -g 10001 /var/cache/musebridge

ENV MUSEBRIDGE_RETRIEVAL_MODE=hybrid \
    MUSEBRIDGE_VECTOR_INDEX=/var/cache/musebridge/embeddings-v1.json

USER 10001:10001
