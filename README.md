# ReAct 问答服务

一个基于 FastAPI 构建、带有 ReAct 风格执行引擎骨架的可扩展问答服务。

## 功能特性

- 提供基于 REST 的 API，遵循 OpenAPI 3.0，使用 JSON 请求与响应
- 内置 Swagger UI，访问地址为 `/swagger`
- 使用 Redis 保存会话状态：每个会话默认保留最近 10 轮对话（共 20 条消息）
- 会话 ID 使用 UUIDv4
- 安全能力
  - JWT 鉴权
  - 限流：默认 5 次请求/秒
  - 敏感操作二次确认机制

## 项目结构

项目目录结构位于 `react-qa-service/` 下，按服务端、前端、测试等模块组织。

## 本地运行（Docker）

1. 基于示例文件创建 `.env`：

```bash
cp .env.example .env
```

2. 启动服务：

```bash
docker compose up --build
```

3. 打开 Swagger：

- `http://localhost:8000/swagger`

## 前端（React + Vite）

如果需要使用简单的 Web 聊天界面：

```bash
npm install
npm run dev
```

然后打开 `http://localhost:5173`，你可以：

- 使用演示账号登录（默认是 `admin` / `admin`）
- 将文件拖拽到聊天输入区域上传并建立索引，支持 `.txt/.md/.csv/.json/.log/.pdf/.docx`
- 提问后，前端会携带 JWT 调用 `/api/v1/chat/qa`

如果前端运行在不同的开发端口或没有配置代理，例如 `3000`，请在前端环境变量中设置：

```bash
VITE_API_BASE_URL=http://localhost:8000
```

## 文档入库接口

- `POST /api/v1/docs/index`
  - 用于提交原始文本 JSON 数据并建立索引
- `POST /api/v1/docs/upload`
  - 用于上传 multipart 文件
  - 必填字段：`file`
  - 可选字段：`doc_id`、`metadata_json`

## API 快速开始

### 1. 获取 JWT

接口：

`POST /api/v1/auth/login`

请求体：

```json
{ "username": "admin", "password": "admin" }
```

### 2. 发起聊天

接口：

`POST /api/v1/chat/`

请求头：

`Authorization: Bearer <token>`

请求体：

```json
{ "message": "你好，介绍一下 ReAct 是什么？" }
```

### 3. 敏感操作确认

第一次调用时，如果未携带 `confirm_token`，接口会返回 **202** 和 `confirm_token`：

```json
{ "message": "我要执行删除", "action": "delete", "action_input": { "id": "123" } }
```

第二次调用时，携带 `confirm_token` 继续执行：

```json
{
  "message": "确认执行删除",
  "session_id": "<same-session-id>",
  "action": "delete",
  "confirm_token": "<confirm-token-from-202>"
}
```

## 质量与测试

这个项目本质上是一个 **FastAPI（Python）** 服务。同时仓库中也提供了 `package.json`，作为统一的质量检查入口，便于在本地或 CI 中使用一致的命令执行 lint、格式化、类型检查和测试。

### 安装开发依赖

建议先创建虚拟环境，再安装依赖：

```bash
python -m pip install -r requirements.txt
```

### 运行测试

运行全部测试：

```bash
python -m pytest -q
```

只运行认证相关测试：

```bash
python -m pytest -q tests/test_auth.py
```

只运行聊天相关测试：

```bash
python -m pytest -q tests/test_chat.py
```

### 使用统一质量命令（npm scripts）

如果你希望通过统一入口执行检查，可以运行：

```bash
npm run validate
```

可用脚本包括：

- `npm run lint` / `npm run lint:fix`：使用 `ruff`
- `npm run format` / `npm run format:check`：使用 `black`
- `npm run typecheck`：使用 `mypy`
- `npm run test` / `npm run test:auth` / `npm run test:chat`
- `npm run test:coverage`：使用 `pytest-cov`，生成覆盖率报告到 `htmlcov/`
