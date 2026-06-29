FROM python:3.13-slim

WORKDIR /app

# Install only core deps (skip heavy ML libs for API-only deployment)
COPY pyproject.toml .
RUN pip install --no-cache-dir numpy networkx openai httpx fastapi uvicorn beautifulsoup4

# Copy source code
COPY llm/ llm/
COPY domain/ domain/
COPY parser/ parser/
COPY content_analysis/ content_analysis/
COPY retrieval/ retrieval/
COPY agent/ agent/
COPY api/ api/
COPY deeprag/ deeprag/
COPY config.py .

# Health check
HEALTHCHECK --interval=30s --timeout=10s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
