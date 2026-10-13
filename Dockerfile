# One image, two services (see docker-compose.yml): the MCP server and the Analyst Console.

# Stage 1: build the console's web app. Only the built files go into the final image.
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web ./
RUN npm run build


FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY warehouse ./warehouse


RUN cd warehouse && DBT_PROFILES_DIR=. dbt build --quiet

# What the console needs on top of the MCP server: eval results, demo traces, the built web app,
# and a folder for paused runs (a volume in docker-compose.yml, so pauses survive a redeploy).
COPY evals ./evals
COPY traces ./traces
COPY --from=web /web/dist ./web/dist
RUN mkdir -p checkpoints


RUN useradd --create-home appuser && chown -R appuser /srv
USER appuser

ENV MCP_TRANSPORT=streamable-http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=8)"

CMD ["python", "-m", "app.mcp_server.server"]
