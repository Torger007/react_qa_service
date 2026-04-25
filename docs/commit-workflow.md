# 提交协作规范

本文档服务于双设备开发和高频同步场景。核心原则只有一句话：`小步快提交，但每次提交都必须单一、可解释、可回滚。`

## 1. 分支策略

- 主分支保持为 `master`
- 常规任务从 `master` 拉短期分支
- 分支完成后尽快合回，不保留长期堆积的功能分支

推荐分支命名：

- `feat-xxx`
- `fix-xxx`
- `refactor-xxx`
- `docs-xxx`
- `chore-xxx`

如果本地环境对带 `/` 的分支名兼容不好，统一使用连字符命名。

## 2. commit 粒度

推荐：

- 一个 commit 只表达一件事
- 一个 commit 最好能在一句话内讲清楚
- 达到最小可运行点就 commit
- 需要切换设备前先 commit，再 push

不推荐：

- “修一个 bug，顺手重构两层，再改一组样式”
- 把代码改动、文档改动、测试改动混成互不相关的杂糅提交
- 把明显未完成的半套重构直接同步到另一台设备

## 3. commit message 规则

使用轻量前缀，不强制完整 conventional commits。

推荐格式：

```text
type: short summary
```

允许的 `type`：

- `feat`
- `fix`
- `refactor`
- `test`
- `docs`
- `chore`

示例：

- `fix: refresh session list after delete`
- `refactor: isolate auth session invalidation flow`
- `test: cover postgres read backend fallback`
- `docs: add codex harness engineering rules`

## 4. 提交前最低要求

提交前至少确认以下几点：

- 工作树中的改动属于同一个目标
- 临时调试代码、日志、注释没有误留
- 这次改动对应的最小验证已完成，或已知未验证项已清楚记录
- 如果行为变了，相关测试或文档至少同步一项

## 5. 推送节奏

适合当前项目的建议节奏：

- 本地完成最小闭环后立刻 commit
- 当天跨设备切换前 push
- 不等“整个功能完全做完”才同步
- 如果当前状态只是实验，不要 push 到会污染主线的分支

## 6. 和 Codex 配合时的要求

如果任务准备交给 `Codex` 执行，任务描述里建议显式加一句：

```md
请保持改动单一目的，便于拆分 commit；不要顺手做无关重构。
```

如果你预计会分多次提交，建议把任务拆成：

1. 修复或实现
2. 补测试
3. 补文档

这样每一步都更容易同步和回滚。

## 7. 后续自动化预留

第一版不强制启用 `git hook`，但后续可以按下面的边界逐步接入：

- `pre-commit`：只做超轻检查，不阻塞高频开发
- `pre-push`：再接入较重的验证，如 `npm run validate` 或 `npm run build`

在没有自动化之前，本文档和 [手工自检清单](./manual-checklist.md) 作为默认流程。
