FROM python:3.11-slim
WORKDIR /app
COPY riskmemory ./riskmemory
COPY web ./web
ENV HOST=0.0.0.0
ENV PORT=8765
EXPOSE 8765
CMD ["sh", "-c", "python -m riskmemory.server --host 0.0.0.0 --port ${PORT} --no-browser"]
