# react-qa-service 实施状态

本文汇总当前 `react-qa-service` 在 Agent 优化、上线准备、登录体系和前端产品化改造方面的实际进展，便于继续开发和对外同步。

## 当前已完成

- 结构化切块能力已落地
  - 支持标题、段落、列表、表格、分页感知切块
  - 已写入 `section_title`、`order`、`page`、`heading_path`、`chunk_kind`
- Agent 任务分流已落地
  - 支持 `qa` / `summary` 分类
  - 前端 pending 状态会预判任务类型，避免总结请求等待时一直显示成 QA
- Summary 链路已完成多轮优化
  - 中等长度文档优先走单次 LLM 直读总结
  - 超长文档走并发 map-reduce
  - 仅在 LLM 超时或失败时回退到 fallback summary
- 检索增强已接入
  - multi-query
  - rerank
  - 去重与相邻 chunk 合并
- 健康检查与上线辅助能力已完成
  - `GET /api/v1/ops/health`
  - `GET /api/v1/ops/readiness`
- Feedback 链路可用，且已增加 TTL 保留策略
- 人工验收主流程基本通过
  - 登录
  - 健康检查
  - 文档上传
  - QA
  - Summary
  - Feedback

## 本轮完成

### 正式用户体系

- 登录系统已从“演示多用户”升级到“正式用户体系”
- 用户数据已持久化到 Redis
- 密码已改为 PBKDF2 哈希存储，不再使用明文运行时鉴权
- JWT 已携带角色信息，权限判断不再依赖固定用户名
- 应用启动时会根据引导配置自动初始化默认管理员或初始用户
- 新增用户相关接口
  - `POST /api/v1/auth/register`
  - `GET /api/v1/auth/me`
  - `GET /api/v1/auth/users`
  - `POST /api/v1/auth/users`
  - `PATCH /api/v1/auth/users/{username}`
  - `POST /api/v1/auth/me/password`
- 公开注册接口已放开为匿名可访问，但注册结果固定为普通用户

### 前端界面优化

- 主标题已调整为“智能文档问答系统”
- 登录 / 注册入口已移动到左上角
- 左侧栏改为历史对话记录区
- 左侧栏底部新增“新添对话”按钮
- 支持删除指定历史会话
- 知识库文档上传已支持拖拽到聊天框区域
- 右侧区域已收敛为轻量“会话概览”，不再展示大量调试细节

### Summary 调用方式修复

- 修复了 summary 专用超时没有真正透传到底层 LLM 请求的问题
- 修复了 summary 请求在线程取消时无法及时放弃的问题
- 优化了 summary 调用策略
  - 小中型文档优先单次 LLM 总结，发挥 `qwen3.5-plus` 长文本理解能力
  - 超过阈值再进入并发 map-reduce

## 当前验证结果

- 后端测试通过
  - `B:\python\anaconda\envs\qa\python.exe -m pytest -q`
  - 当前结果：`37 passed`
- Python 语法检查通过
  - `python -m compileall app tests`
- 前端生产构建通过
  - `npm run build`
- 应用启动与健康检查已验证通过
  - `POST /api/v1/auth/login` -> `200`
  - `GET /api/v1/ops/health` -> `200`
  - `GET /api/v1/ops/readiness` -> `200`
- 正式用户体系补充测试已覆盖
  - 默认管理员可登录
  - 公开注册可创建普通用户
  - 管理员可创建用户并列出用户
  - 普通用户不能创建用户
  - 用户可自助修改密码
  - 管理员可禁用用户

## 当前已知问题 / 风险

- 默认系统 `python` 不一定是项目实际验证通过的环境
- 当前推荐使用：
  - `B:\python\anaconda\envs\qa\python.exe`
- `ops` 端点当前也受 JWT 保护，验收时需要先登录获取 token
- 如仍使用默认 `admin/admin`，启动与 readiness 会继续给出 warning
- 当前历史对话记录仍存储在浏览器本地
  - 适合当前阶段的前端体验
  - 如需跨设备同步，后续应迁移到后端持久化
- 当前用户体系仍依赖 Redis 作为持久化层
  - 适合当前项目阶段和中小规模场景
  - 如后续需要更强的一致性、审计和运维能力，建议迁移到专门数据库

## 下一步建议

1. 为历史对话记录增加后端持久化与跨设备同步
2. 为登录体系补充刷新 token、注销和会话失效能力
3. 增加登录失败计数、账号锁定和审计日志
4. 为用户管理补充更完整的后台页面
5. 规划从 Redis 用户存储迁移到正式数据库方案
6. 完成生产环境配置替换
   - `jwt_secret`
   - `openai_api_key`
   - `VITE_API_BASE_URL`
