# 验证与复现

执行日期为 2026 年 11 月 13 日至 14 日。机器环境、代码版本和数据 SHA256 记录在 `evaluation.json` 和 `ollama_evaluation.json`。

## 算法评价

使用 UCI Sentiment Labelled Sentences 的 Yelp 子集共 1000 条英文句子。数据采用 CC BY 4.0，创建者为 Dimitrios Kotzias 等人。评价使用全部 1000 条，没有在这批数据上训练或调整阈值。预训练词典的历史开发数据可能与公开评论有重叠，结果按公开基准报告。[数据与许可](https://archive.ics.uci.edu/dataset/331/sentiment+labelled+sentences)

下载官方 ZIP 并取出 `sentiment labelled sentences/yelp_labelled.txt`，然后运行。

```powershell
python -m backend.evaluate --sentiment-tsv PATH_TO_YELP_TSV --output docs/validation/evaluation.json
```

`agreement` 的分母包含全部记录，unknown 和 mixed 预测计为未匹配。`coverage` 表示输出正面或负面的比例。macro F1 计算正面与负面两类，unknown 预测计入对应真实类别的漏报。

人工筛查标签可采用以下 JSONL 结构。

```json
{"review":{"source":"amap","poi_id":"p1","content":"原始评论文本","rating":"4"},"screening_label":"retained"}
```

```powershell
python -m backend.evaluate --labelled-reviews LABELLED_JSONL --output var/screening-evaluation.json
```

评价脚本支持独立标签的指标计算，当前提交的结果中筛查状态为待标注。所有生成性能样本均在代码内构造。

本地模型可复用当前应用的配置进行分层抽样评价。以下命令每类抽取 50 条，随机种子默认为 20261113，结果记录原始行号、错误和同一样本上的 VADER 指标。`configured` 模式默认跳过生成输入的性能循环，避免额外执行大量模型请求。

```powershell
$env:DINELENS_ANALYSIS_MODE='ollama'
$env:DINELENS_OLLAMA_MODEL='qwen2.5:3b'
$env:DINELENS_OLLAMA_URL='http://127.0.0.1:11435'
python -m backend.evaluate --engine configured --sentiment-tsv PATH_TO_YELP_TSV --sample-per-label 50 --output var/model-evaluation.json
```

模型评价调用实际报告分析路径，筛除、unknown、mixed 和推理错误均计为整体情感未匹配。公开数据可能出现在模型预训练语料中，结果用于公开基准对照。中文实际来源的方面情感及可疑评论筛查仍需要独立人工标签。

## 功能测试

```powershell
python -m pytest -q
$env:DINELENS_BROWSER_TESTS='1'
python -m pytest -q --junitxml=var/validation/junit.xml
```

浏览器测试默认选择 Edge，可设置 `DINELENS_BROWSER_CHANNEL=chrome`。它启动隔离的本地服务和数据库，覆盖搜索、分店确认、分析报告、分享访问、撤销以及删除历史。可设置 `DINELENS_SCREENSHOT_PATH` 保存报告页面截图。

真实模型的构造案例回归可使用以下命令，覆盖正负观点并存、环境与服务区分、正常差评保留、明确促销筛查和原文引用。配置本地模型后执行。

```powershell
$env:DINELENS_OLLAMA_TESTS='1'
$env:DINELENS_OLLAMA_URL='http://127.0.0.1:11435'
$env:DINELENS_OLLAMA_MODEL='qwen2.5:3b'
python -m pytest backend/test_local_model_live.py -q
```

通知回归包含版本 1 数据库升级、并发轮询、分页、已读持久化、权限和故障后的恢复。浏览器流程同时检查已读刷新和删除后的通知状态。

需求覆盖情况与外部来源检查见 `docs/personal_delivery.md`。第三方 Starlette 当前对 httpx 测试客户端给出弃用提示，测试仍通过。

## 模型模式实测

最终版本为 `ollama:qwen2.5:3b:aspects_v5_rules_v1`。在完整 UCI Yelp 1000 条上，一致率为 97.0%，负面召回为 96.6%，macro F1 为 0.9798。本轮格式或引用校验错误为 0。评价约耗时 430.6 秒，其中包含逐条模型调用和报告分析。规则模式在同一数据上的一致率为 67.4%。

结果记录全部行号和各模块代码 SHA256。开发过程使用构造案例修正方面提示、差评误筛和引用校验，并在公开样本上发现引用生成错误后改用原文候选约束。这批公开数据用于工程评估和回归。独立中文业务标签仍需另行准备。

浏览器流程还单独使用真实 Qwen 模型执行一次，覆盖页面、API、模型、数据库、通知、分享和删除。来源输入为固定构造评论。真实来源的人工验证和登录检查另列在 `test_results.json`。

本机真实来源验证从名称搜索开始，获得 20 个候选，确认 `B023B09325` 后采集并分析 200 条唯一评论。报告、历史、站内通知、分享和撤销均成功，全程约 403.9 秒。待独立标注的 200 条真实评论位于 `var/validation/annotation-input.jsonl`，`screening_label` 初始为空。
