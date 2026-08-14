# 前端构建阶段（使用公司内网镜像源，Docker Hub 在本机不可达）
FROM public-docker-virtual.xzrobot.com/node:18-alpine AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# 后端运行阶段（后端自动挂载 /app/frontend/dist 提供前端页面）
FROM public-docker-virtual.xzrobot.com/python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8093
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY backend/marketplace_mcp ./marketplace_mcp
COPY backend/scripts ./scripts
COPY --from=frontend /build/frontend/dist /app/frontend/dist
EXPOSE 8093
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8093} --workers 1"]
