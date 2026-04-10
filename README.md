# react-qa-service

一个基于 FastAPI + React 的文档问答与总结服务，支持文档上传、向量检索、QA、Summary、Feedback 和 Agent 轨迹展示。

## 主要能力

- 文档上传与索引：
  - 支持 `.txt` `.md` `.csv` `.json` `.log` `.pdf` `.docx`
- 文档 QA：
  - 基于检索结果回答问题
  - 返回引用片段和 Agent 轨迹
- 文档 Summary：
  - 小中型文档优先走单次 LLM 直读总结
  - 超长文档走并发 map-reduce
  - 超时或失败时自动回退到 fallback summary
- Agent 可观测性：
  - `task_type`
  - `retrieval_summary`
  - `rerank_summary`
  - `summary_phase`
  - `tool_calls`
- 健康检查：
  - `/api/v1/ops/health`
  - `/api/v1/ops/readiness`

## 目录结构

- `app/` 后端服务
- `src/` 前端界面
- `tests/` 测试
- `docs/` 项目文档
- `scripts/` 评测脚本

## 环境要求

- Python 3.11+
- Node.js 18+
- Redis

当前本地验证推荐使用：

```powershell
B:\python\anaconda\envs\qa\python.exe
```

## 后端启动

安装依赖：

```powershell
python -m pip install -r requirements.txt
```

启动服务：

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Swagger:

- `http://127.0.0.1:8000/swagger`

## 前端启动

安装依赖：

```powershell
npm install
```

开发模式：

```powershell
npm run dev
```

生产构建：

```powershell
npm run build
```

如前端未通过代理转发后端，请配置：

```bash
VITE_API_BASE_URL=http://127.0.0.1:8000
```

## 配置项

关键配置位于 [app/core/config.py](/b:/agent/MyCode/react-qa-service/app/core/config.py)：

- `openai_api_key`
- `openai_base_url`
- `llm_model`
- `llm_timeout_seconds`
- `summary_timeout_seconds`
- `summary_single_pass_chars`
- `summary_max_parallelism`
- `summary_max_chunks`
- `summary_group_size`
- `embedding_model`
- `redis_url`
- `jwt_secret`

## API 快速使用

### 1. 登录获取 token

```http
POST /api/v1/auth/login
Content-Type: application/json

{
  "username": "admin",
  "password": "admin"
}
```

### 2. 健康检查

注意：当前 `ops` 接口也需要 JWT。

```http
GET /api/v1/ops/health
Authorization: Bearer <token>
```

```http
GET /api/v1/ops/readiness
Authorization: Bearer <token>
```

### 3. 上传文档

```http
POST /api/v1/docs/upload
Authorization: Bearer <token>
Content-Type: multipart/form-data
```

### 4. QA / Summary

统一入口：

```http
POST /api/v1/chat/qa
Authorization: Bearer <token>
Content-Type: application/json

{
  "message": "请总结全文",
  "top_k": 4
}
```

接口会根据问题自动分流到 `qa` 或 `summary`。

## 测试

运行全部测试：

```powershell
python -m pytest -q
```

当前已验证结果：

- `31 passed`

## 当前状态

当前实施状态见：

- [docs/implementation-status.md](/b:/agent/MyCode/react-qa-service/docs/implementation-status.md)
