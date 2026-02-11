# Stage 1: Build frontend
FROM node:22-slim AS frontend
ARG COMMIT_SHA=dev
WORKDIR /build
COPY app/package.json app/tsconfig.json app/vite.config.ts app/index.html ./
RUN npm install
COPY app/src/ src/
RUN VITE_COMMIT_SHA=${COMMIT_SHA} npm run build

# Stage 2: Server with built frontend baked in
FROM python:3.12-slim
ARG COMMIT_SHA=dev
WORKDIR /app
RUN pip install --no-cache-dir fastapi uvicorn[standard] aiodocker httpx websockets cryptography
COPY server/server.py .
COPY --from=frontend /build/dist /app/static
ENV COMMIT_SHA=${COMMIT_SHA}

ENV REMOLT_STATIC_DIR=/app/static
EXPOSE 8080
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080"]
