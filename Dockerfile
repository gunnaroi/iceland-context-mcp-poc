FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN mkdir -p /app/data && pip install --no-cache-dir .
ENV ICELAND_CONTEXT_DATA_DIR=/app/data
ENV MCP_TRANSPORT=streamable-http MCP_HOST=0.0.0.0 MCP_PORT=8000
EXPOSE 8000
CMD ["iceland-context-mcp"]
