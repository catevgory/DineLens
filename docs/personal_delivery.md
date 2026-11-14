# Yangyi 后端与 AI 开发交付说明

本次工作基于 `feature/yangyi`，起点为 `82ce8d976fe74a678c80d277168241520f713a22`。职责依据既有讨论中 Member 3 的评论分析与评分方向，以及 Task 3 的报告对象、UC4 至 UC7、历史分享和需求追踪分工。

## 实现范围

| 责任 | 实现位置 | 已验证行为 |
| --- | --- | --- |
| 清洗 去重 筛查 | `backend/analysis.py` | 来源与分店隔离 数字评分解析 促销理由 去重计数 |
| 方面情感 原文摘要 | `backend/analysis.py` `backend/local_model.py` | 中英规则 多方面 引文逐字校验 Ollama 原文候选约束 |
| 评分与评估 | `backend/analysis.py` `backend/evaluate.py` | 均值 贝叶斯 时间衰减 Wilson 区间 0 1 4 5 边界 |
| 结果存储 | `backend/storage.py` `backend/migrations/` | 迁移重放 事务回滚 唯一历史 证据到期 |
| 历史与分享 | `backend/app.py` `backend/storage.py` | 所有权隔离 CSRF 只读 到期 撤销 删除 |
| 页面接入 | `web/analysis.*` `web/report.*` `web/api.js` | 搜索 确认 报告 历史 分享与撤销 |
| 流水线协作 | `backend/pipeline.py` `backend/job_manager.py` | 真实采集接线 取消 检查点恢复 原接口响应 |

对 `fetchers/browser_fetch.py` 的修改集中在可选取消回调、检查点、高德登录跳转恢复和地图脚本故障时的同会话地点查询回退。现有搜索与采集方法继续由采集模块提供。隧道工具与 Pages 代理沿用仓库实现。

## 交付清单

1. 算法及后端模块已经接入 `backend.app:app`，并提供 `python -m backend.manage analyse` 处理既有 JSONL 输出。
2. 依赖保留在 `requirements.txt`，测试依赖单列 `requirements-dev.txt`，启动和配置说明见 `backend/README.md`。
3. 数据库迁移位于 `backend/migrations/`，当前版本为 2，启动自动应用，`migrate` 命令可重复执行。
4. OpenAPI 由 `/docs` 和 `/openapi.json` 提供，接口路径、权限与状态约定见后端说明。
5. 需求测试覆盖原取消回归、算法边界、持久化、权限、共享、恢复、CLI 和浏览器流程。
6. 实测算法结果位于 `docs/validation/evaluation.json`，使用分析代码 SHA256、数据 SHA256 与模型版本标识运行条件。
7. 演示步骤及来源协作接口列在下文。

## 原有 15 个草稿的处理

`dinelens/analysis.py` 中的文本规范化、局部否定、多方面划分和评分思想经过适配进入 `backend/analysis.py`。实际输入继续使用现有采集字段。`.gitignore` 中必要的缓存和运行数据规则补入仓库现有文件。

其余草稿中的独立包结构、SQLAlchemy 数据层、Alembic 环境、HTTP 来源适配器、包启动入口和独立配置系统留在上层目录供参考。本次数据库使用标准库 SQLite，现有项目通过 `requirements.txt` 和 `backend.app:app` 启动。测试数据按现有字段构造，未将示例评论接入产品来源。

## 需求追踪

| 工作需求 | 验证依据 |
| --- | --- |
| DL 01 02 分店与来源 | 搜索参数校验 已确认 branch_id 跨分店记录拒收 采集接线测试 |
| DL 03 至 07 分析与评分 | `test_analysis.py` 与公开情感基准 |
| DL 08 唯一结果 | 幂等重复提交 轮询唯一历史 事务失败回滚 |
| DL 09 取消 | 原 6 项取消测试与实际流水线的采集和分析阶段取消 |
| DL 10 恢复 | 报告重启读取 已采集检查点恢复 同数据库 worker 排他 |
| DL 11 至 14 权限 历史 分享 | 两组匿名会话隔离 清 Cookie 后新历史 分享只读 撤销 过期 删除 |
| DL 15 失败路径 | 来源待人工处理 模型输出失败 事务失败，失败任务可受控重试 |

DL 15 已接入站内报告通知，包含持久化、未读数、已读状态、所有者隔离、分页与页面轮询。通知读取时幂等补齐报告完成事件，失败后由下一次轮询重试。独立事务保证通知故障期间报告仍然可读。回归测试覆盖故障注入、重启、重复请求、旧数据库升级、权限和删除。

## 演示步骤

1. 按后端说明安装依赖并启动服务，打开 `/web/analysis.html`。
2. 输入城市和餐厅名称，核对地址后确认分店，观察 queued、collecting、analysing 与 done。
3. 打开报告，检查保留及排除数量、有效评分、方面观点、原文引用和算法版本。
4. 创建分享链接，在另一个浏览器会话中打开，再撤销链接并刷新共享页面。
5. 返回历史，删除对应条目，检查报告与分享停止访问。
6. 在来源或模型处理期间取消任务，检查任务停止且历史列表没有新增报告。
7. 重启服务，检查原历史仍可读取。使用 CLI 对失败任务重新排队可演示受控恢复。

`test_browser.py` 自动验证其中的搜索至报告、分享、撤销和删除路径。它使用固定采集输入，实际运行后续系统层。手工实时演示需要完成平台验证并提供有效来源。

## 实测结果与限制

最终完整运行通过 50 项测试及 10 个子测试，子测试包含 4 个评分边界和 6 个真实模型构造案例。JUnit 共记录 60 个通过案例，包含无界面 Edge 的浏览器端到端流程。JavaScript 语法检查、Python 编译检查、依赖一致性检查和 Git 差异空白检查均通过。测试清单与真实来源诊断见 `docs/validation/test_results.json`。

公开 UCI Yelp 情感数据共 1000 句，英文规则模式一致率为 67.4%，macro F1 为 0.7453。正面召回为 82.6%，负面召回为 52.2%，有 213 句输出 unknown，1 句输出 mixed。公开数据的标签为整体句子情感，Qwen2.5 3B 模型模式在同一 1000 句上的一致率为 97.0%，macro F1 为 0.9798，正面召回为 97.4%，负面召回为 96.6%。预测包含 1 条 unknown、5 条 neutral 和 14 条 mixed，全部计入未匹配。本轮没有推理格式或引用校验错误，整轮耗时约 430.6 秒。完整结果见 `docs/validation/ollama_evaluation.json`。

200 条生成输入的 20 次纯分析耗时记录在 JSON 中。该计时范围包含分析步骤，采集、网络与模型推理应另行测量。

实际高德验证使用 README 的 `B023B09325` 楼外楼示例。用户在可见浏览器完成人工验证后，后台复用 profile 成功搜索到 20 个候选，采集 200 条唯一评论，并完成 Qwen 分析、报告、历史、通知及分享和撤销验证。全程约 403.9 秒，分析约 351.7 秒，130 条有效数字评分参与综合分。初次运行暴露的登录跳转、搜索地图脚本故障和长引用生成截断均已修复并补充回归。

本机已配置项目内 Ollama 便携运行时、Qwen2.5 3B 和 Vulkan GPU 推理。真实推理发现小模型存在差评误筛，因此两种模式共用保守筛查，模型负责方面情感。模型仅输出原文片段编号，后端还原引用并逐字校验，用于防止改写引用和长引用生成截断，校验错误最多修正一次。真实模型已验证中英构造案例及 API、数据库、站内通知链路。85% 筛查一致率仍需独立人工标签集，标注应记录判断依据和分歧。

## 需要组员协作的接口

- 采集负责人继续提供 `fetch_amap`、`fetch_baidu` 及规范化评论，确认评分量纲、稳定来源 ID、日期含义和可用登录 profile。取消检查回调接收零参数，取消时直接抛出异常。
- 前端负责人复用 `web/api.js`，先建立匿名会话，写请求发送 CSRF，并按 `branch_id` 创建任务。Pages 继续转发 `/api/*`。
- 组长确认分享、原文、报告的最终保留期限，以及评分先验和性能测量口径。当前默认期限为 30、90、365 天。
- 评估协作提供独立的可疑模式和方面情感标签。JSONL 标注格式可使用 `review` 和 `screening_label`，评估 CLI 会计算各类指标。

待人工标注的真实评论共 200 条，位于本机 `var/validation/annotation-input.jsonl`。文件仅填入原评论和空的 `screening_label`，可复制给独立标注者。标注完成后使用评价 CLI 计算筛查指标，标注数据留在忽略目录中。

## 后续更新（2026-11-15）

- 规则筛查改为始终先跑，结果分为 `retained` / `flagged` / `uncertain`；`SUSPICIOUS_PROMOTION` 预路由只在“写/代写/刷/招 + 好评/评论/评价”与“微信/联系/返现/下单”等邀约词同时出现时触发。
- LLM 改为可选且只处理 `uncertain` 评论；LLM 关闭或不可用时这些评论被排除，不进入评分和摘要，报告写入 `counts.uncertain`、`UNCERTAIN_REVIEWS_EXCLUDED`、`LLM_DISABLED` / `LLM_UNAVAILABLE`。明确 retained/flagged 的评论不再交给模型。
- 开关保存在 `var/settings.json`，通过 `GET/PUT /settings`（`X-Admin-Token`）读写；对之后创建的任务生效。实现位于 `backend/settings.py`、`backend/screening.py`、`backend/analysis.py`、`backend/local_model.py`、`backend/pipeline.py`。
- 新增只监听 `127.0.0.1:8765` 的本地控制面板 `admin/`，提供后端/前端/隧道/Pages/LLM/数据库状态、LLM 配置测试和日志查看；`start_all.bat` 负责启动。后端新增 `/status` 和轮转日志 `var/logs/backend.log`。
- 当前默认测试为 60 passed、2 skipped；本地 Qwen2.5 7B Q4_K_M 的 opt-in live test 为 1 passed + 6 subtests（2026-11-15）。
- 上文 50 项测试、UCI 评估、85% 筛查和真实高德耗时数据产生于混合筛查/控制面板之前；引用这些数字前需要重新跑评估。
