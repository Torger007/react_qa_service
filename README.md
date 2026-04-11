# react-qa-service

基于 FastAPI + React 的智能文档问答系统，支持知识库文档上传、检索增强问答、文档总结、反馈回收，以及正式用户体系、会话管理和 PostgreSQL 迁移能力。

## 主要能力

- 文档上传与索引
  - 支持 `.txt`、`.md`、`.csv`、`.json`、`.log`、`.pdf`、`.docx`
- 文档问答
  - 基于检索结果进行 QA
  - 返回引用片段与 Agent Trace
- 文档总结
  - 中等长度文档优先单次 LLM 总结
  - 超长文档支持 map-reduce
- 正式用户体系
  - PBKDF2 密码哈希
  - JWT 角色鉴权
  - refresh token / logout / logout-all
  - 登录失败计数、账号锁定、审计日志
- 历史会话
  - 后端持久化
  - 跨设备同步
  - 删除指定会话
- 管理后台
  - 用户列表、创建、禁用、删除、批量删除
  - 审计日志查看
- PostgreSQL 迁移能力
  - 用户主数据
  - refresh token 会话
  - 认证审计日志
  - 支持双写与读切换

## 目录结构

- `app/` 后端服务
- `src/` 前端应用
- `tests/` 测试
- `docs/` 项目文档
- `migrations/` Alembic 迁移

## 环境要求

- Python 3.11+
- Node.js 18+
- Redis
- PostgreSQL

推荐本地解释器：

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

Swagger：

- `http://127.0.0.1:8000/swagger`

## 前端启动

```powershell
npm install
npm run dev
```

生产构建：

```powershell
npm run build
```

## PostgreSQL 初始化

项目已接入 Alembic 和初始 schema。

1. 配置 `DATABASE_URL`
2. 安装依赖
3. 执行迁移

推荐命令：

```powershell
& "B:\python\anaconda\envs\qa\python.exe" -m pip install -r requirements.txt
& "B:\python\anaconda\envs\qa\python.exe" -m alembic upgrade head
```

如果你使用系统环境变量而不是 `.env`，先确认当前终端能读到：

```powershell
echo $env:DATABASE_URL
```

## 关键配置

### 认证与安全

- `JWT_SECRET`
- `JWT_ALGORITHM`
- `ACCESS_TOKEN_TTL_SECONDS`
- `REFRESH_TOKEN_TTL_SECONDS`
- `LOGIN_MAX_FAILURES`
- `LOGIN_LOCKOUT_SECONDS`

### 引导管理员

- `ADMIN_USERNAME`
- `ADMIN_PASSWORD`
- `DEMO_USERS_JSON`

说明：

- `ADMIN_USERNAME` / `ADMIN_PASSWORD` 用于初始化管理员账号
- `DEMO_USERS_JSON` 可用于初始化一组引导用户

### Redis

- `REDIS_URL`
- `REDIS_PREFIX`
- `SESSION_TTL_SECONDS`

### PostgreSQL / 迁移

- `DATABASE_URL`
- `AUTH_STORAGE_BACKEND`
- `AUTH_DUAL_WRITE_ENABLED`
- `AUTH_READ_BACKEND`

推荐迁移阶段配置：

```env
AUTH_STORAGE_BACKEND=redis
AUTH_DUAL_WRITE_ENABLED=true
AUTH_READ_BACKEND=redis
```

含义：

- Redis 仍作为主写后端
- PostgreSQL 同步双写
- 读取先保持 Redis

完成真实读切换验收后，可切为：

```env
AUTH_STORAGE_BACKEND=redis
AUTH_DUAL_WRITE_ENABLED=true
AUTH_READ_BACKEND=postgres
```

当前项目已验证：

- 登录读取可走 PostgreSQL
- refresh token 校验可走 PostgreSQL
- logout / logout-all 后会话失效可在 PostgreSQL 读路径下正常生效
- 审计日志查询可走 PostgreSQL

说明：

- access token 撤销黑名单仍保留在 Redis，适合短 TTL 场景

### LLM / RAG

- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`
- `LLM_MODEL`
- `LLM_TEMPERATURE`
- `LLM_TIMEOUT_SECONDS`
- `SUMMARY_TIMEOUT_SECONDS`
- `SUMMARY_SINGLE_PASS_CHARS`
- `SUMMARY_MAX_PARALLELISM`
- `SUMMARY_MAX_CHUNKS`
- `SUMMARY_GROUP_SIZE`
- `EMBEDDING_MODEL`
- `EMBEDDING_BATCH_SIZE`

### 前端

- `VITE_API_BASE_URL`

本地开发常用值：

```env
VITE_API_BASE_URL=http://127.0.0.1:8000
```

## API 示例

登录：

```http
POST /api/v1/auth/login
Content-Type: application/json

{
  "username": "admin",
  "password": "admin"
}
```

刷新 token：

```http
POST /api/v1/auth/refresh
Content-Type: application/json

{
  "refresh_token": "<refresh_token>"
}
```

获取当前用户：

```http
GET /api/v1/auth/me
Authorization: Bearer <token>
```

统一 QA / Summary 入口：

```http
POST /api/v1/chat/qa
Authorization: Bearer <token>
Content-Type: application/json

{
  "message": "请总结全文",
  "top_k": 4
}
```

## 验证结果

最近一次回归结果：

- `B:\python\anaconda\envs\qa\python.exe -m pytest -q`
  - `67 passed`
- `B:\python\anaconda\envs\qa\python.exe -m compileall app tests`
  - 通过
- `npm run build`
  - 最近一次前端构建通过

## 相关文档

- [implementation-status.md](/b:/agent/MyCode/react-qa-service/docs/implementation-status.md)
- [auth-migration-plan.md](/b:/agent/MyCode/react-qa-service/docs/auth-migration-plan.md)
- [daily-summary-2026-04-10.md](/b:/agent/MyCode/react-qa-service/docs/daily-summary-2026-04-10.md)
