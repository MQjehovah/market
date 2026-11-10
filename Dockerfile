# 前端构建阶段（使用公司内网镜像源，Docker Hub 在本机不可达）
FROM public-docker-virtual.xzrobot.com/node:18-alpine AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# DWS CLI 构建阶段：npm 包内置 linux 二进制，只取二进制进运行镜像
FROM public-docker-virtual.xzrobot.com/node:18-alpine AS dws
ARG DWS_VERSION=1.0.62
RUN npm i -g dingtalk-workspace-cli@${DWS_VERSION} \
    && mkdir -p /dws \
    && tar -xzf /usr/local/lib/node_modules/dingtalk-workspace-cli/assets/dws-linux-amd64.tar.gz -C /dws \
    && /dws/dws --version

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
COPY --from=dws /dws/dws /usr/local/bin/dws
RUN chmod +x /usr/local/bin/dws && dws --version
EXPOSE 8093
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8093} --workers 1"]
