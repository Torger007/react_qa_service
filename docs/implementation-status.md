# react-qa-service 实施状态

本文汇总当前 `react-qa-service` 在 Agent 优化、上线准备和人工验收方面的实际进展，便于继续开发和对外同步。

## 当前已完成

- 结构化切块能力已落地：
  - 支持标题、段落、列表、表格、分页感知切块
  - 已写入 `section_title`、`order`、`page`、`heading_path`、`chunk_kind`
- Agent 任务分流已落地：
  - 支持 `qa` / `summary` 分类
  - 前端 pending 状态也会预判任务类型，避免总结请求在等待阶段始终显示为 QA
- Summary 链路已完成多轮优化：
  - 支持 summary 专用链路，不再复用 QA 回答链路
  - 中等长度文档优先走单次 LLM 直读总结
  - 超长文档走并发 map-reduce
  - 若 LLM 超时或失败，才回退到 fallback summary
- 检索增强已接入：
  - multi-query
  - rerank
  - 去重与相邻 chunk 合并
- 健康检查与上线辅助能力已完成：
  - `GET /api/v1/ops/health`
  - `GET /api/v1/ops/readiness`
- Feedback 链路可用，且已增加 TTL 保留策略
- 前端 inspector 已展示：
  - `task_type`
  - `retrieval_summary`
  - `rerank_summary`
  - `summary_phase`
  - 工具调用轨迹

## 本轮关键修复

### Summary 调用方式修复

- 修复了 summary 专用超时没有真正透传到底层 LLM 请求的问题
- 修复了 summary 请求在线程取消时无法及时放弃的问题
- 优化了 summary 调用策略：
  - 小中型文档优先单次 LLM 总结，发挥 `qwen3.5-plus` 长文本理解能力
  - 超过阈值再进入并发 map-reduce

### Summary 相关配置

已在 [app/core/config.py](/b:/agent/MyCode/react-qa-service/app/core/config.py) 增加：

- `llm_timeout_seconds`
- `summary_timeout_seconds`
- `summary_max_parallelism`
- `summary_single_pass_chars`
- `summary_max_chunks`
- `summary_group_size`

## 当前验证结果

- 后端测试通过：
  - `B:\python\anaconda\envs\qa\python.exe -m pytest -q`
  - 当前结果：`31 passed`
- Python 语法检查通过：
  - `python -m compileall app tests`
- 前端生产构建通过：
  - `npm run build`
- 应用启动与健康检查已验证通过：
  - `POST /api/v1/auth/login` -> `200`
  - `GET /api/v1/ops/health` -> `200`
  - `GET /api/v1/ops/readiness` -> `200`

## 当前已知问题 / 风险

- 默认系统 `python` 不一定是项目实际验证通过的环境
- 当前推荐使用：
  - `B:\python\anaconda\envs\qa\python.exe`
- `ops` 端点当前也受 JWT 保护，验收时需要先登录获取 token
- 启动时仍会给出两条 warning：
  - `jwt_secret` 仍是默认占位值
  - `demo_username/demo_password` 仍是 `admin/admin`

## 下一步建议

1. 继续做人工烟测，重点验证真实文档下的 Summary 效果
2. 如有需要，为 Summary 单独配置模型，和 QA/Planner 分开
3. 完成生产环境配置替换：
   - `jwt_secret`
   - `demo_username`
   - `demo_password`
   - `openai_api_key`
   - `VITE_API_BASE_URL`
4. 跑离线评测脚本并留存基线：
   - `scripts/eval_qa.py`
   - `scripts/eval_summary.py`
   - `scripts/eval_retrieval.py`
