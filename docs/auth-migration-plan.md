# Redis 到 PostgreSQL 认证体系迁移方案

## 目标

将当前基于 Redis 的用户、refresh token 会话和认证审计日志体系迁移到 PostgreSQL，形成更适合长期演进的正式存储架构，同时尽量不影响现有登录、问答和管理功能。

本方案覆盖：

- 用户主数据迁移
- refresh token / 会话数据迁移
- 认证审计日志迁移
- 历史会话元数据后续入库方向
- 双写、读切换和回滚策略

## 当前落地状态

当前仓库已经完成：

- PostgreSQL schema 与 Alembic 初始化
- `users`、`auth_refresh_sessions`、`auth_audit_logs`、`chat_sessions`、`chat_messages` 表模型
- `PostgresUserRepository`
- `PostgresTokenSessionRepository`
- `PostgresAuditLogRepository`
- `UserService`、`TokenSessionService`、`AuditLogService` 的双写与读切换支持
- 真实读切换验收

当前推荐迁移配置：

```env
AUTH_STORAGE_BACKEND=redis
AUTH_DUAL_WRITE_ENABLED=true
AUTH_READ_BACKEND=postgres
```

说明：

- Redis 仍为主写
- PostgreSQL 已同步双写
- 读取已可切到 PostgreSQL
- access token 撤销黑名单仍保留在 Redis

## 目标架构

- PostgreSQL
  - 用户主数据
  - refresh token 会话
  - 认证审计日志
  - 历史会话元数据
  - 历史消息正文
- Redis
  - access token 黑名单
  - 限流
  - 临时确认 token
  - 短 TTL 缓存

## 数据表设计

## 1. `users`

- `id` `uuid` 主键
- `username` `varchar(128)` 唯一
- `password_hash` `text`
- `role` `varchar(32)`
- `is_active` `boolean`
- `last_login_at` `timestamptz null`
- `last_failed_login_at` `timestamptz null`
- `failed_login_attempts` `integer`
- `locked_until` `timestamptz null`
- `token_version` `integer`
- `created_at` `timestamptz`
- `updated_at` `timestamptz`

## 2. `auth_refresh_sessions`

- `id` `uuid` 主键
- `user_id` `uuid` 外键 -> `users.id`
- `jti` `varchar(128)` 唯一
- `token_version` `integer`
- `issued_at` `timestamptz`
- `expires_at` `timestamptz`
- `revoked_at` `timestamptz null`
- `ip_address` `varchar(64) null`
- `user_agent` `text null`
- `device_label` `varchar(128) null`
- `created_at` `timestamptz`

## 3. `auth_audit_logs`

- `id` `bigserial` 主键
- `event_type` `varchar(64)`
- `user_id` `uuid null`
- `username_snapshot` `varchar(128) null`
- `actor_user_id` `uuid null`
- `actor_username_snapshot` `varchar(128) null`
- `outcome` `varchar(32)`
- `ip_address` `varchar(64) null`
- `details_json` `jsonb`
- `created_at` `timestamptz`

## 4. `chat_sessions`

- `id` `uuid` 主键
- `owner_user_id` `uuid` 外键 -> `users.id`
- `title` `varchar(255)`
- `last_message_preview` `text`
- `message_count` `integer`
- `created_at` `timestamptz`
- `updated_at` `timestamptz`

## 5. `chat_messages`

- `id` `uuid` 主键
- `session_id` `uuid` 外键 -> `chat_sessions.id`
- `role` `varchar(16)`
- `content` `text`
- `created_at` `timestamptz`
- `metadata_json` `jsonb`

## 迁移阶段

## 阶段 1：数据库底座

已完成：

- PostgreSQL 连接与配置
- Alembic 初始化
- 初始 schema 落地

## 阶段 2：仓储抽象

已完成：

- 用户仓储抽象与 PostgreSQL 实现
- refresh token 会话仓储抽象与 PostgreSQL 实现
- 审计日志仓储抽象与 PostgreSQL 实现

## 阶段 3：双写

已完成：

- 用户主数据双写
- refresh token 会话双写
- 审计日志双写

## 阶段 4：读切换

已完成验收：

- `AUTH_READ_BACKEND=postgres`
- 登录
- `/auth/me`
- `/auth/refresh`
- `/auth/logout`
- `/auth/logout-all`
- `/auth/audit-logs`

结果：全部符合预期。

## 阶段 5：主写切换

尚未执行，建议作为下一阶段推进：

1. 先在灰度环境中将 `AUTH_STORAGE_BACKEND=postgres`
2. 保留 `AUTH_DUAL_WRITE_ENABLED=true`
3. 保持 Redis 黑名单能力
4. 观察一段时间后再考虑收缩 Redis 认证主数据职责

## 双写策略

推荐原则：

- 主链路成功以主写后端为准
- 双写失败需要记录错误与告警，但不立即阻断主链路

当前实现状态：

- Redis 主写时，PostgreSQL 作为同步副本
- PostgreSQL 主写能力已经具备，但尚未正式切主

## 回滚策略

如果 PostgreSQL 读路径出现问题：

1. 将 `AUTH_READ_BACKEND=redis`
2. 保持 `AUTH_DUAL_WRITE_ENABLED=true`
3. 修复 PostgreSQL 问题后重新校验一致性

如果未来 PostgreSQL 主写后出现问题：

1. 将 `AUTH_STORAGE_BACKEND=redis`
2. 保持双写
3. 通过一致性对账确认数据完整性

## 已解决的迁移问题

## 1. Alembic 连接串密码被隐藏

问题：

- SQLAlchemy URL 在字符串化时会把密码渲染成 `***`
- 导致 Alembic 实际拿到的不是正确密码

现状：

- 已在 [session.py](/b:/agent/MyCode/react-qa-service/app/db/session.py) 中修复

## 2. 读切换配置下 bootstrap 启动冲突

问题：

- 当 `AUTH_STORAGE_BACKEND=redis`
- `AUTH_DUAL_WRITE_ENABLED=true`
- `AUTH_READ_BACKEND=postgres`
- bootstrap 先按读后端判断用户不存在，再按 Redis 主写创建，触发 `User already exists`

现状：

- 已在 [user_service.py](/b:/agent/MyCode/react-qa-service/app/services/user_service.py) 中修复
- bootstrap 现在优先检查主写后端，双写开启时再补查另一侧后端

## 下一步建议

1. 将历史会话元数据与消息正文继续迁移到 PostgreSQL repository。
2. 增加一致性巡检脚本，对 Redis / PostgreSQL 的用户、refresh session、audit log 进行对账。
3. 规划 PostgreSQL 主写灰度切换与回滚演练。
