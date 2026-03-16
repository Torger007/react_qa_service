# Agent 智能体优化方案

## 1. 背景

当前 `react-qa-service` 已经完成了 LangGraph 版文档问答智能体的首期接入，具备以下能力：

- 支持上传文档并建立向量索引
- 支持基于 Redis 会话历史进行多轮问答
- 支持 LangGraph 驱动的 `prepare_context -> plan_next_step -> retrieve_documents -> generate_answer` 执行流程
- 支持前端展示 agent 状态与工具卡

但从实际使用效果看，当前智能体仍然存在明显短板：

- RAG 检索仍然是基础的向量相似度检索，召回与排序质量有限
- chunk 方式是按字符滑窗切分，缺乏语义和结构意识
- “问答”和“总结”复用同一条链路，导致总结效果接近片段拼接
- planner 的任务理解能力较弱，没有形成真正的任务分流和策略选择
- 没有 rerank、文档级聚合、摘要链路等关键能力

因此需要在当前项目基础上，制定一套面向第二阶段的 agent 智能体优化方案。

---

## 2. 现状分析

### 2.1 当前 RAG 形态

当前项目采用的是最基础的一种 RAG：

- 检索方式：Query Embedding + Vector Similarity Search
- 向量库：Redis 中存储 chunk 向量
- 搜索算法：Python 层线性扫描 + cosine similarity
- 返回结果：Top-K 相关片段

这是一种典型的基础检索式 RAG，适合做简单问答验证，但不适合复杂总结、长文理解、多文档归纳和高质量结构化输出。

### 2.2 当前 chunk 方式

当前 chunk 逻辑在 `app/core/vector_store.py` 中实现，特点是：

- 按字符长度切分
- `chunk_size=800`
- `chunk_overlap=200`
- 不识别标题、段落、章节、列表、表格等结构

这种方式的优点是实现快、逻辑简单；缺点是：

- 语义边界经常被截断
- 章节上下文容易丢失
- 摘要场景下会变成“拼片段”
- 检索虽然能命中局部内容，但难以重建整篇文档的逻辑

### 2.3 当前 agent 的主要问题

当前 agent 逻辑虽然已经是 LangGraph，但仍然更接近“有状态包装的基础 RAG”，主要问题如下：

1. planner 只判断“检索还是回答”，没有识别任务类型
2. 总结任务仍走 top-k 检索问答链路，不适合长文摘要
3. 没有文档级处理逻辑，模型看到的是离散片段，不是完整文档
4. 没有 rerank、聚合、去重和覆盖控制
5. 没有质量评估和效果观测闭环

---

## 3. 优化目标

本轮优化的核心目标不是继续“堆工具”，而是把当前 agent 从“基础问答机器人”升级为“真正具备文档理解与任务分流能力的智能体”。

### 目标拆解

1. 提升文档问答的准确性和依据质量
2. 明显提升长文总结、提炼要点、风险归纳等任务效果
3. 让 agent 能识别任务类型，并自动选择合适链路
4. 保持现有接口兼容，避免大规模重构前端
5. 为后续扩展多工具智能体打好架构基础

---

## 4. 优化方向

### 4.1 方向一：升级 chunk 策略

这是所有效果优化的基础。

#### 当前问题

- 字符滑窗切分缺少文档结构意识
- 一个逻辑段落可能被拆散到多个 chunk
- 检索出来的结果缺少上下文完整性

#### 目标方案

将当前 chunk 升级为“结构感知切分”，优先基于文档自然结构切分，再做长度控制。

建议优先支持：

- 标题 / 小节
- 段落
- 列表
- 表格（保留为文本块）
- 页码 / 顺序信息

#### 预期收益

- 提升问答片段相关性
- 为总结链路提供更完整的语义块
- 为后续 parent-child retrieval、章节聚合打基础

---

### 4.2 方向二：将“总结任务”从普通 QA 链路中拆分

这是当前体验问题最直接的突破口。

#### 当前问题

现在无论用户问：

- “这篇文档讲了什么？”
- “请总结全文”
- “提炼风险点”

系统都会走：

- 检索几个相关 chunk
- 直接生成答案

这对问答场景基本可用，但对总结场景天然不足。

#### 目标方案

引入任务分类与专用链路：

- `qa`：面向具体问题，走检索问答
- `summary`：面向全文总结，走摘要链路
- `extract`：面向字段抽取，走结构化抽取链路
- `compare`：面向对比分析，走多段聚合链路

首期至少实现：

- `qa`
- `summary`

#### 推荐 summary 链路

采用 Map-Reduce Summary：

1. 取文档全量或大范围 chunk
2. 对 chunk 分组做局部摘要
3. 对多个局部摘要做二次汇总
4. 输出最终摘要、关键结论、风险/行动项

#### 预期收益

- 总结不再只是检索片段拼接
- 对长文档会有明显改善
- 能体现 agent 的任务策略能力

---

### 4.3 方向三：升级检索策略

#### 当前问题

- 只有单路 embedding 检索
- 没有重排序
- 没有覆盖控制
- 容易召回多个重复片段

#### 目标方案

逐步升级为“召回 + 重排 + 聚合”模式。

建议优先级：

1. 去重与同文档聚合
2. 增加 metadata-aware 排序
3. 增加 rerank 层
4. 增加多查询改写（multi-query）

#### 具体思路

- 召回层：
  - 保留现有 embedding 检索
  - 增加 query rewrite / multi-query 作为可选策略
- 排序层：
  - 引入 reranker 或轻量二次打分
- 聚合层：
  - 相邻 chunk 合并
  - 同一 doc 下按 section/order 聚合

#### 预期收益

- 减少重复片段
- 提高上下文完整性
- 提高回答连贯性和总结质量

---

### 4.4 方向四：增强 LangGraph 的任务编排能力

#### 当前问题

当前图结构过于单一，只能完成最基础的 ReAct。

#### 目标方案

将 LangGraph 从“单链路执行器”升级为“任务编排器”。

推荐 graph 演进：

- `prepare_context`
- `classify_task`
- `plan_next_step`
- `retrieve_documents`
- `rerank_results`
- `summarize_document`
- `extract_structured_data`
- `generate_answer`
- `finalize_trace`

其中首批真正落地的节点建议为：

- `classify_task`
- `retrieve_documents`
- `summarize_document`
- `generate_answer`

#### 预期收益

- agent 能根据用户意图选择不同策略
- summary 和 QA 不再混用一条路径
- 后续扩展多工具更加自然

---

### 4.5 方向五：完善观测、评估与质量闭环

#### 当前问题

当前只能看到回答结果和简单 trace，缺少效果评估机制。

#### 目标方案

补充以下观测能力：

- 每轮 agent 执行耗时
- 检索命中率
- top-k 使用情况
- summary 链路执行轮次
- 工具调用成功率
- 用户反馈（赞/踩）与会话关联

同时建立离线评估样本集：

- QA benchmark
- Summary benchmark
- Risk extraction benchmark

#### 预期收益

- 可以量化优化效果
- 避免“感觉上变好了，实际上不可控”

---

## 5. 可执行开发任务清单

以下任务按照建议优先级拆解，适合按阶段推进。

### 阶段一：结构化 chunk 升级

#### Task 1.1 重构文档切分器

- 目标：替换当前 `make_chunks_from_text()` 的字符滑窗逻辑
- 内容：
  - 新增结构感知切分器
  - 按段落/标题优先切块
  - 保留长度上限与 overlap 兜底
- 输出：
  - 新的 chunk builder
  - chunk metadata 增强字段

代码落点：

- 核心改造文件：
  - [app/core/vector_store.py](/b:/agent/MyCode/react-qa-service/app/core/vector_store.py)
- 重点修改点：
  - 替换 `make_chunks_from_text()`
  - 新增结构化切分辅助函数，例如标题切分、段落切分、长度兜底切分
- 可能新增文件：
  - `app/services/chunking.py` 或 `app/core/chunking.py`
- 联动文件：
  - [app/api/v1/endpoints/docs.py](/b:/agent/MyCode/react-qa-service/app/api/v1/endpoints/docs.py)
  - [app/services/document_loader.py](/b:/agent/MyCode/react-qa-service/app/services/document_loader.py)

#### Task 1.2 增加 chunk metadata

- 目标：让检索结果携带更多结构信息
- 内容：
  - 增加 `section_title`
  - 增加 `order`
  - 增加 `page`
  - 增加 `heading_path`
- 输出：
  - 更新索引结构
  - 更新文档入库逻辑

代码落点：

- 核心改造文件：
  - [app/core/vector_store.py](/b:/agent/MyCode/react-qa-service/app/core/vector_store.py)
- 重点修改点：
  - 扩展 `DocumentChunk.metadata`
  - 在 chunk 生成阶段写入 `section_title/order/page/heading_path`
- 联动文件：
  - [app/api/v1/endpoints/docs.py](/b:/agent/MyCode/react-qa-service/app/api/v1/endpoints/docs.py)
  - [app/models/qa_schemas.py](/b:/agent/MyCode/react-qa-service/app/models/qa_schemas.py)
  - [app/services/document_agent_service.py](/b:/agent/MyCode/react-qa-service/app/services/document_agent_service.py)

#### Task 1.3 补充 chunk 测试

- 目标：确保切分质量稳定
- 内容：
  - 标题文档切分测试
  - 长段落切分测试
  - 表格文本测试
  - overlap 边界测试

代码落点：

- 新增测试文件建议：
  - `tests/test_chunking.py`
- 复用或扩展：
  - [tests/conftest.py](/b:/agent/MyCode/react-qa-service/tests/conftest.py)

---

### 阶段二：总结链路落地

#### Task 2.1 增加任务分类节点

- 目标：区分 QA 与 Summary
- 内容：
  - 新增 `classify_task` 节点
  - 输出 `qa` / `summary`
  - 在 graph 中加入路由逻辑

代码落点：

- 核心改造文件：
  - [app/services/document_agent_service.py](/b:/agent/MyCode/react-qa-service/app/services/document_agent_service.py)
- 重点修改点：
  - 扩展 `AgentState`
  - 新增 `classify_task` 节点
  - 修改 `_build_graph()` 路由
- 联动文件：
  - [app/models/qa_schemas.py](/b:/agent/MyCode/react-qa-service/app/models/qa_schemas.py)
  - [src/App.tsx](/b:/agent/MyCode/react-qa-service/src/App.tsx)

#### Task 2.2 实现 summary tool / summary node

- 目标：支持文档级总结
- 内容：
  - 拉取全文或大范围 chunk
  - 分组摘要
  - 汇总摘要
- 输出：
  - `summarize_document` 节点
  - 可复用的 summary prompt

代码落点：

- 核心改造文件：
  - [app/services/document_agent_service.py](/b:/agent/MyCode/react-qa-service/app/services/document_agent_service.py)
- 可能新增文件：
  - `app/services/summary_service.py`
  - `app/prompts/summary_prompts.py`
- 联动文件：
  - [app/core/vector_store.py](/b:/agent/MyCode/react-qa-service/app/core/vector_store.py)
  - [app/api/v1/endpoints/qa.py](/b:/agent/MyCode/react-qa-service/app/api/v1/endpoints/qa.py)

#### Task 2.3 为总结任务设计输出模板

- 目标：提升总结质量与稳定性
- 内容：
  - 标准摘要
  - 关键要点
  - 风险/问题
  - 下一步建议

代码落点：

- 推荐新增文件：
  - `app/prompts/summary_prompts.py`
- 联动文件：
  - [app/services/document_agent_service.py](/b:/agent/MyCode/react-qa-service/app/services/document_agent_service.py)
  - [src/App.tsx](/b:/agent/MyCode/react-qa-service/src/App.tsx)

#### Task 2.4 补充总结任务测试

- 目标：验证总结链路可用
- 内容：
  - 长文总结测试
  - “总结全文”路由测试
  - 无足够片段时降级测试

代码落点：

- 核心测试文件：
  - [tests/test_qa_agent.py](/b:/agent/MyCode/react-qa-service/tests/test_qa_agent.py)
- 建议新增：
  - `tests/test_summary_agent.py`

---

### 阶段三：检索质量优化

#### Task 3.1 增加检索结果去重与聚合

- 目标：减少重复 chunk
- 内容：
  - 同文档相邻 chunk 合并
  - 重复片段过滤
  - 按文档顺序聚合

代码落点：

- 核心改造文件：
  - [app/services/document_agent_service.py](/b:/agent/MyCode/react-qa-service/app/services/document_agent_service.py)
- 可能新增文件：
  - `app/services/retrieval_postprocess.py`
- 联动文件：
  - [app/core/vector_store.py](/b:/agent/MyCode/react-qa-service/app/core/vector_store.py)

#### Task 3.2 增加 rerank 层

- 目标：提升最终上下文质量
- 内容：
  - 引入轻量 rerank 模型或 LLM rerank
  - 对召回结果做二次排序

代码落点：

- 可能新增文件：
  - `app/services/reranker.py`
- 核心接线文件：
  - [app/services/document_agent_service.py](/b:/agent/MyCode/react-qa-service/app/services/document_agent_service.py)
  - [app/main.py](/b:/agent/MyCode/react-qa-service/app/main.py)
- 配置扩展位置：
  - `app/core/config.py`

#### Task 3.3 增加多查询改写

- 目标：提高召回覆盖率
- 内容：
  - planner 生成改写 query
  - 多 query 检索并合并结果

代码落点：

- 核心改造文件：
  - [app/services/document_agent_service.py](/b:/agent/MyCode/react-qa-service/app/services/document_agent_service.py)
- 可能新增文件：
  - `app/services/query_rewrite_service.py`
- 联动文件：
  - [app/models/qa_schemas.py](/b:/agent/MyCode/react-qa-service/app/models/qa_schemas.py)

#### Task 3.4 检索效果评估

- 目标：量化检索改进
- 内容：
  - 建立问答样本集
  - 统计 top-k 命中率
  - 对比优化前后结果

代码落点：

- 推荐新增目录：
  - `tests/evals/`
  - `scripts/`
- 推荐新增文件：
  - `scripts/eval_retrieval.py`
  - `tests/evals/retrieval_cases.json`

---

### 阶段四：LangGraph agent 深化

#### Task 4.1 重构 graph 状态

- 目标：支持更多任务节点
- 内容：
  - 扩展 `AgentState`
  - 增加 `task_type`
  - 增加 `aggregated_context`
  - 增加 `summary_drafts`

代码落点：

- 核心改造文件：
  - [app/services/document_agent_service.py](/b:/agent/MyCode/react-qa-service/app/services/document_agent_service.py)
- 联动文件：
  - [app/models/qa_schemas.py](/b:/agent/MyCode/react-qa-service/app/models/qa_schemas.py)

#### Task 4.2 扩展 graph 节点

- 目标：让 graph 支持多任务路径
- 内容：
  - `classify_task`
  - `retrieve_documents`
  - `rerank_results`
  - `summarize_document`
  - `generate_answer`

代码落点：

- 核心改造文件：
  - [app/services/document_agent_service.py](/b:/agent/MyCode/react-qa-service/app/services/document_agent_service.py)
- 可拆分新增文件：
  - `app/services/agent_nodes.py`
  - `app/services/agent_prompts.py`

#### Task 4.3 增强 trace 结构

- 目标：让前端能更准确展示 agent 行为
- 内容：
  - 增加 task_type
  - 增加 retrieval summary
  - 增加 summary phase 信息

代码落点：

- 核心改造文件：
  - [app/models/qa_schemas.py](/b:/agent/MyCode/react-qa-service/app/models/qa_schemas.py)
  - [app/services/document_agent_service.py](/b:/agent/MyCode/react-qa-service/app/services/document_agent_service.py)
- 前端联动：
  - [src/App.tsx](/b:/agent/MyCode/react-qa-service/src/App.tsx)

---

### 阶段五：前端与质量闭环

#### Task 5.1 前端增加任务态展示

- 目标：前端区分 QA 与 Summary 执行路径
- 内容：
  - 工具卡展示真实检索与摘要步骤
  - inspector 展示 task_type

代码落点：

- 核心改造文件：
  - [src/App.tsx](/b:/agent/MyCode/react-qa-service/src/App.tsx)
  - [src/styles.css](/b:/agent/MyCode/react-qa-service/src/styles.css)
- 后端配合文件：
  - [app/models/qa_schemas.py](/b:/agent/MyCode/react-qa-service/app/models/qa_schemas.py)

#### Task 5.2 增加质量反馈采集

- 目标：形成优化数据来源
- 内容：
  - 记录点赞/点踩
  - 关联 session / task type / trace

代码落点：

- 前端入口：
  - [src/App.tsx](/b:/agent/MyCode/react-qa-service/src/App.tsx)
- 后端建议新增：
  - `app/api/v1/endpoints/feedback.py`
  - `app/models/feedback_schemas.py`
  - `app/services/feedback_service.py`
- 应用接线：
  - [app/api/v1/router.py](/b:/agent/MyCode/react-qa-service/app/api/v1/router.py)

#### Task 5.3 增加离线评估脚本

- 目标：形成可重复评估能力
- 内容：
  - 问答样本评估
  - 总结样本评估
  - 检索质量评估

代码落点：

- 推荐新增目录：
  - `scripts/`
  - `tests/evals/`
- 推荐新增文件：
  - `scripts/eval_qa.py`
  - `scripts/eval_summary.py`
  - `scripts/eval_retrieval.py`
  - `tests/evals/qa_cases.json`
  - `tests/evals/summary_cases.json`

---

## 5.1 任务与代码落点总览

为了便于执行，可以将任务与代码落点汇总为以下模块责任：

### 后端核心模块

- agent 编排核心：
  - [app/services/document_agent_service.py](/b:/agent/MyCode/react-qa-service/app/services/document_agent_service.py)
- RAG 与 chunk 基础设施：
  - [app/core/vector_store.py](/b:/agent/MyCode/react-qa-service/app/core/vector_store.py)
  - [app/services/document_loader.py](/b:/agent/MyCode/react-qa-service/app/services/document_loader.py)
- QA 接口层：
  - [app/api/v1/endpoints/qa.py](/b:/agent/MyCode/react-qa-service/app/api/v1/endpoints/qa.py)
- 应用初始化与依赖注入：
  - [app/main.py](/b:/agent/MyCode/react-qa-service/app/main.py)
- agent trace / schema：
  - [app/models/qa_schemas.py](/b:/agent/MyCode/react-qa-service/app/models/qa_schemas.py)

### 前端核心模块

- agent 展示与工具卡：
  - [src/App.tsx](/b:/agent/MyCode/react-qa-service/src/App.tsx)
- 状态与视觉样式：
  - [src/styles.css](/b:/agent/MyCode/react-qa-service/src/styles.css)

### 测试与评估模块

- 当前 agent 单测入口：
  - [tests/test_qa_agent.py](/b:/agent/MyCode/react-qa-service/tests/test_qa_agent.py)
- 测试基础设施：
  - [tests/conftest.py](/b:/agent/MyCode/react-qa-service/tests/conftest.py)
- 建议新增评估目录：
  - `tests/evals/`
  - `scripts/`

---

## 6. 建议实施顺序

建议按照下面顺序推进，而不是同时铺开：

### P0

- Task 1.1 重构文档切分器
- Task 2.1 增加任务分类节点
- Task 2.2 实现 summary node

### P1

- Task 1.2 增加 chunk metadata
- Task 2.3 设计总结输出模板
- Task 3.1 检索去重与聚合

### P2

- Task 3.2 增加 rerank
- Task 3.3 增加 multi-query
- Task 4.2 扩展 graph 节点

### P3

- Task 5.2 增加反馈闭环
- Task 5.3 增加离线评估

---

## 7. 预期最终效果

完成以上优化后，目标效果应当从“能回答”提升到“能理解并组织回答”：

- 问答更 grounded，依据更稳定
- 总结不再是简单片段拼接
- agent 能识别任务并选对路径
- trace 更有解释力，前端展示更真实
- 后续扩展搜索、数据库、代码执行等工具时，架构不会推倒重来

---

## 8. 结论

当前项目已经具备 agent 化基础，但还处于“基础 RAG + 简化 ReAct”的阶段。下一步优化重点不应放在继续增加工具数量，而应优先解决以下三个核心问题：

1. chunk 质量
2. summary 专用链路
3. 检索与任务编排质量

这三者解决之后，项目才能从“可用的问答 demo”走向“真正具备文档理解能力的智能体系统”。
