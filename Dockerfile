# /health git_commit. Builds from a git clone keep .git in the context;
# without one (tarball, worktree) this records "unknown".
FROM alpine/git:2.54.0 AS rev
RUN --mount=type=bind,target=/src \
    git -c safe.directory='*' -C /src rev-parse --short HEAD > /git_commit 2>/dev/null \
    || echo unknown > /git_commit

FROM python:3.12-slim

# ca-certificates: httpx2 (fastmcp 4) uses the OS trust store.
RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY mcp_bildsprache/ ./mcp_bildsprache/
COPY --from=rev /git_commit /app/.git_commit

RUN pip install --no-cache-dir uv && \
    uv export --frozen --no-dev --no-emit-project -o /tmp/requirements.txt && \
    pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt && \
    pip install --no-cache-dir --no-deps . && \
    rm /tmp/requirements.txt && \
    addgroup --system mcp && adduser --system --ingroup mcp mcp

USER mcp

ENV TRANSPORT=http
ENV HOST=0.0.0.0

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python3 -c "import urllib.request,json,sys; r=urllib.request.urlopen('http://localhost:8000/health',timeout=3); d=json.loads(r.read()); sys.exit(0 if d.get('status')=='healthy' else 1)"

CMD ["mcp-bildsprache"]
