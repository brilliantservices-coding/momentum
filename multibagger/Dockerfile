# Claude Code CLI for the "subscription" LLM provider (needs Node 22+, newer than Debian ships).
FROM node:22-slim AS node
RUN npm install -g @anthropic-ai/claude-code@2.1.319 && npm cache clean --force

FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MB_BACKEND=gcp DISABLE_AUTOUPDATER=1 \
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
# The package ships a native binary (bin/claude.exe, put in place by its postinstall); there is no cli.js.
RUN ln -s /usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe /usr/local/bin/claude \
    && claude --version
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY agents.yaml ./
COPY mb ./mb
COPY portal ./portal
# The portal and guard services run this; agent jobs override it with `python -m mb.runner <agent>`.
CMD exec uvicorn portal.app:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips='*'
