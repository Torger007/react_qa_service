# react-qa-service 实施状态

本文档用于记录当前项目在问答能力、登录体系、会话持久化和 PostgreSQL 迁移方面的真实进展，并给出下一阶段建议。

## 当前总体状态

- 文档问答主流程可用
  - 文档上传
  - QA
  - Summary
  - Feedback
- 检索增强链路已完成多轮优化
  - LLM query rewrite
  - LLM rerank
  - Summary 相关性优先选块
  - 相邻 chunk 放宽合并
- 正式用户体系已落地
  - Redis 持久化用户
  - PBKDF2 密码哈希
  - JWT 角色鉴权
  - 注册 / 登录 / 改密 / 用户管理
- 历史会话后端持久化与跨设备同步已完成首轮落地
- refresh token / logout / 会话失效能力已完成首轮落地
- 登录失败计数、账号锁定、审计日志已完成首轮落地
- 用户管理后台页面已完成首轮落地
- PostgreSQL 底座、用户仓储、refresh token 会话仓储、审计日志仓储都已接入
- PostgreSQL 读切换验收已完成，结果符合预期

## 已完成能力

## 1. 文档问答与总结链路

- 结构化切块支持标题、段落、列表、表格、分页感知
- Agent 已支持 `qa` / `summary` 分流
- Summary 优先走单次 LLM 总结，在内容规模可控时不轻易退化为 map-reduce
- 检索后处理已支持更宽松的相邻 chunk 合并

## 2. 正式用户体系

- 用户持久化到 Redis
- 密码使用 PBKDF2 哈希保存
- JWT 携带角色信息
- 已提供接口：
  - `POST /api/v1/auth/login`
  - `POST /api/v1/auth/token`
  - `POST /api/v1/auth/register`
  - `GET /api/v1/auth/me`
  - `GET /api/v1/auth/users`
  - `POST /api/v1/auth/users`
  - `PATCH /api/v1/auth/users/{username}`
  - `DELETE /api/v1/auth/users/{username}`
  - `POST /api/v1/auth/users/bulk-delete`
  - `POST /api/v1/auth/me/password`

## 3. 历史会话后端持久化与跨设备同步

- Redis 中新增会话元数据和用户会话索引
- 会话元数据包含：
  - `session_id`
  - `owner_username`
  - `title`
  - `created_at`
  - `updated_at`
  - `last_message_preview`
  - `message_count`
- 已新增会话接口：
  - `GET /api/v1/chat/sessions`
  - `GET /api/v1/chat/sessions/{session_id}`
  - `DELETE /api/v1/chat/sessions/{session_id}`
- `/api/v1/chat/` 与 `/api/v1/chat/qa` 已增加会话归属校验
- 前端历史会话列表已接入后端接口

## 4. refresh token / logout / 会话失效

- Access token 已包含 `jti`、`type`、`token_version`
- 已新增接口：
  - `POST /api/v1/auth/refresh`
  - `POST /api/v1/auth/logout`
  - `POST /api/v1/auth/logout-all`
- 登录成功后同时返回 `access_token` 与 `refresh_token`
- `refresh` 会轮换 refresh token，并重新签发 access token
- `logout` 会注销当前 refresh token，并拉黑当前 access token
- `logout-all` 会使当前用户的全部 refresh 会话失效，并通过 `token_version` 让旧 access token 失效
- 用户改密、用户被禁用等敏感动作会同步触发现有会话失效

## 5. 登录失败计数、账号锁定和审计日志

- 用户模型新增：
  - `failed_login_attempts`
  - `last_failed_login_at`
  - `locked_until`
- 登录失败会累加失败次数，并在达到阈值后自动锁定账号
- 登录成功会清零失败计数并解除锁定
- 新增配置项：
  - `LOGIN_MAX_FAILURES`
  - `LOGIN_LOCKOUT_SECONDS`
  - `AUDIT_LOG_MAX_ENTRIES`
  - `AUDIT_LOG_DEFAULT_LIMIT`
- 已新增管理员审计查询接口：
  - `GET /api/v1/auth/audit-logs`

## 6. 用户管理后台页面

- 前端右侧工作台已接入管理员视图
- 已提供：
  - 用户列表查看
  - 用户搜索
  - 创建用户
  - 启用 / 禁用用户
  - 单个删除
  - 批量删除
  - 锁定状态、失败次数、最后登录时间展示
  - 审计日志查看

## 7. PostgreSQL 接入

- 已新增 PostgreSQL 配置：
  - `DATABASE_URL`
  - `AUTH_STORAGE_BACKEND`
  - `AUTH_DUAL_WRITE_ENABLED`
  - `AUTH_READ_BACKEND`
- 已新增数据库底座：
  - `app/db/base.py`
  - `app/db/models.py`
  - `app/db/session.py`
- 已新增 Alembic：
  - `alembic.ini`
  - `migrations/env.py`
  - `migrations/versions/20260411_000001_initial_postgres_schema.py`
- 已修复 Alembic 使用 `DATABASE_URL` 时密码被 SQLAlchemy 自动打码的问题

## 8. PostgreSQL 双写与读切换

- 已新增 PostgreSQL 仓储：
  - `app/repositories/user_repository.py`
  - `app/repositories/token_session_repository.py`
  - `app/repositories/audit_log_repository.py`
- 当前服务支持：
  - Redis 主写
  - PostgreSQL 主写
  - Redis -> PostgreSQL 双写
  - PostgreSQL -> Redis 双写
  - 可配置读后端切换
- 已完成真实读切换验收：
  - 登录读取走 PostgreSQL
  - refresh token 校验走 PostgreSQL
  - logout / logout-all 在 PostgreSQL 读路径下正常生效
  - 审计日志查询走 PostgreSQL
- access token 撤销黑名单仍保留在 Redis

## 9. 2026-04-12 修复

- 修复 `AUTH_STORAGE_BACKEND=redis`、`AUTH_DUAL_WRITE_ENABLED=true`、`AUTH_READ_BACKEND=postgres` 组合下的启动问题
- 根因是 bootstrap 初始化时先按读后端判断，再按主写后端创建，导致误判用户不存在后重复创建
- 现已改为：
  - bootstrap 优先检查主写后端
  - 双写开启时再补查另一侧后端
- 同时修复测试环境被本地 PostgreSQL 配置污染的问题，避免 Windows 下 `psycopg + ProactorEventLoop` 干扰 TestClient 回归

## 当前验证结果

- `B:\python\anaconda\envs\qa\python.exe -m alembic upgrade head`
  - 通过
- `B:\python\anaconda\envs\qa\python.exe -m pytest -q`
  - `67 passed`
- `B:\python\anaconda\envs\qa\python.exe -m compileall app tests`
  - 通过
- `npm run build`
  - 最近一次前端构建通过

## 下一步建议

1. 开始规划将用户主数据主读切到 PostgreSQL 后的长期运行监控与一致性校验。
2. 继续把历史会话元数据与消息正文迁移到 PostgreSQL，形成完整的认证 + 会话数据库方案。
3. 为 PostgreSQL 读路径增加运维观测项，例如读源标记、双写失败告警和一致性巡检脚本。
