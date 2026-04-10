# react-qa-service

一个基于 FastAPI + React 的智能文档问答系统，支持知识库文档上传、检索增强问答、文档总结、反馈回收，以及基于 Redis 的正式用户体系。

## 主要能力

- 文档上传与索引
  - 支持 `.txt`、`.md`、`.csv`、`.json`、`.log`、`.pdf`、`.docx`
- 文档 QA
  - 基于检索结果回答问题
  - 返回引用片段与 Agent 轨迹
- 文档 Summary
  - 中小文档优先走单次 LLM 直读总结
  - 超长文档走并发 map-reduce
  - 仅在超时或失败时回退到 fallback summary
- 正式用户体系
  - 用户持久化到 Redis
  - 密码采用 PBKDF2 哈希存储
  - JWT 携带角色信息
  - 支持管理员创建、更新、禁用用户
  - 支持公开注册普通用户
- 前端交互
  - 主标题为“智能文档问答系统”
  - 左上角登录 / 注册切换
  - 左侧栏展示历史对话记录
  - 左侧栏底部支持“新添对话”
  - 支持删除指定历史会话
  - 支持将知识库文件拖拽到聊天框区域上传
  - 右侧仅保留轻量“会话概览”
- 运维接口
  - `/api/v1/ops/health`
  - `/api/v1/ops/readiness`

## 目录结构

- `app/` 后端服务
- `src/` 前端应用
- `tests/` 测试
- `docs/` 项目文档
- `scripts/` 评测脚本

## 环境要求

- Python 3.11+
- Node.js 18+
- Redis

本地验证推荐使用：

```powershell
B:\python\anaconda\envs\qa\python.exe
```

## 启动方式

后端：

```powershell
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

前端：

```powershell
npm install
npm run dev
```

生产构建：

```powershell
npm run build
```

Swagger：

- `http://127.0.0.1:8000/swagger`

如果前端没有通过代理转发后端，请设置：

```bash
VITE_API_BASE_URL=http://127.0.0.1:8000
```

## 关键配置

配置位于 [config.py](/b:/agent/MyCode/react-qa-service/app/core/config.py)。

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

认证相关引导配置：

- `DEMO_USERNAME` / `DEMO_PASSWORD`
  - 首次启动时默认管理员引导账号
- `DEMO_USERS_JSON`
  - 首次启动时的引导用户列表
  - 当 Redis 中不存在这些用户时会自动创建

示例：

```env
DEMO_USERS_JSON=[{"username":"admin","password":"admin123","role":"admin"},{"username":"alice","password":"alice123","role":"user"},{"username":"bob","password":"bob123","role":"user"}]
```

说明：

- 这些环境变量现在是“引导用户配置”，不是运行时直接鉴权来源
- 应用启动后，用户会持久化写入 Redis
- 后续登录、角色判断、改密都基于 Redis 用户数据
- 如果继续使用默认 `admin/admin`，启动和 readiness 仍会给出 warning

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

### 2. 公开注册普通用户

```http
POST /api/v1/auth/register
Content-Type: application/json

{
  "username": "alice",
  "password": "alice1234",
  "role": "admin"
}
```

说明：

- `register` 接口会强制创建为普通用户
- 即使传入 `role=admin`，返回结果也会是 `user`

### 3. 获取当前用户信息

```http
GET /api/v1/auth/me
Authorization: Bearer <token>
```

### 4. 管理员创建用户

```http
POST /api/v1/auth/users
Authorization: Bearer <admin-token>
Content-Type: application/json

{
  "username": "bob",
  "password": "bob12345",
  "role": "user"
}
```

### 5. 用户自助改密

```http
POST /api/v1/auth/me/password
Authorization: Bearer <token>
Content-Type: application/json

{
  "current_password": "old-password",
  "new_password": "new-password"
}
```

### 6. QA / Summary

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

当前已验证：

- `37 passed`
- `npm run build` 通过

## 当前状态

项目实施状态见：

- [implementation-status.md](/b:/agent/MyCode/react-qa-service/docs/implementation-status.md)
