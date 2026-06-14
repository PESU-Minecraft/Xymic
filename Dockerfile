FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim
WORKDIR /app
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev
COPY . .
EXPOSE 10000
ENV WEB_CONCURRENCY=1
ENV GUNICORN_WORKERS=1
ENV GUNICORN_THREADS=1
RUN chmod +x start.sh
CMD ["./start.sh"]

