FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8001

COPY knowledge/__init__.py knowledge/__main__.py knowledge/dataset.py knowledge/errors.py \
     knowledge/http_service.py knowledge/query.py knowledge/retrieval.py ./knowledge/
COPY knowledge/data/aic-seed-v0.1.json ./knowledge/data/aic-seed-v0.1.json

USER 10001:10001
EXPOSE 8001

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python3", "-c", "import os; from urllib.request import ProxyHandler, build_opener; build_opener(ProxyHandler({})).open('http://127.0.0.1:' + os.environ['PORT'] + '/readyz', timeout=2).close()"]

CMD ["python3", "-m", "knowledge.http_service"]
