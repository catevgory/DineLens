# 朱广翔的 DineLens 开发入口

这是朱广翔（GitHub：catevgory）的个人开发仓库，Fork 自 [Jc0876/DineLens](https://github.com/Jc0876/DineLens)，保留上游代码、历史与许可证。

## 当前首先推进的任务

**分析流程异常提示与操作恢复（P1）**

让用户在搜索或分析遇到错误时知道原因、知道下一步，并能恢复查询原任务，避免重复创建分析任务。

请先阅读：[首项任务说明](docs/guangxiang/FIRST_TASK.md)。其中列出了已核对的代码现状、开发范围、实施步骤与 10 个待验收场景。

当前进度：个人仓库与任务说明已建立，功能代码尚未实现，验收结果均为待验证。

## 在哪里开发

- 专用分支：[`codex/guangxiang-error-recovery`](https://github.com/catevgory/DineLens/tree/codex/guangxiang-error-recovery)
- 首先查看：`web/api.js`、`web/analysis.js`、`web/analysis.html`
- 启动与项目全貌：[原项目 README](README.md)
- 接口与状态约定：[backend/README.md](backend/README.md)

先在专用分支完成修改和验证，再整理交付说明。向上游提交 PR 时，明确列出自己的变更与验证结果。

## 已有的另外两项工作

- 独立人工标注与 85% 筛查评估（P1）：取得队友提供的 200 条真实评论后独立标注，另行推进。
- 前端一键部署接入面板（P2）：在异常提示与操作恢复之后推进。

真实评论、浏览器 profile、配置和凭据继续按现有忽略规则留在本地。
