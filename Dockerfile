FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# Dependencies first: this layer is cached until requirements.txt changes.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY warehouse ./warehouse

# Build the warehouse into the image. The seeds are static, so the database
# is fully reproducible, and the container starts ready to serve.
RUN cd warehouse && DBT_PROFILES_DIR=. dbt build --quiet

# Run as a non-root user. It owns /srv because dbt writes logs at startup.
RUN useradd --create-home appuser && chown -R appuser /srv
USER appuser

ENV MCP_TRANSPORT=streamable-http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=8)"

CMD ["python", "-m", "app.mcp_server.server"]
