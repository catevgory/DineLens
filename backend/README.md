# DineLens 后端与分析

后端在原有单 worker、`/jobs` 和三阶段取消接口上接入真实采集、评论分析、持久化报告、匿名历史与受控分享。`backend.app:app` 使用实际流水线。`JobManager` 原有定时构造方式继续支持既有取消测试。

## 启动

在仓库根目录使用 Python 3.10 或更高版本运行。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m backend.manage migrate
.\.venv\Scripts\python.exe -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

打开 `http://127.0.0.1:8000/web/analysis.html` 搜索餐厅、确认分店并创建分析。`/docs` 提供 OpenAPI 文档。服务启动会自动应用数据库迁移，显式 `migrate` 命令用于预先检查数据库。

Linux 与 macOS 可使用 `.venv/bin/python`。实际采集还需要执行 `python -m playwright install chromium`，或安装系统 Edge/Chrome。运行时保持一个 uvicorn worker，同一数据库的第二个 worker 会被进程锁拒绝。

`.env.example` 提供配置参考，配置通过环境变量读取。PowerShell 使用 `$env:变量名='值'`，Bash 使用 `export 变量名='值'`。

## 数据来源与输入

搜索复用 `main.search`、AMap/Baidu 官方元数据查询和现有浏览器搜索。高德地图脚本未加载出候选时，使用同一浏览器会话查询地点接口，并保留验证码处理。采集复用 `fetchers.browser_fetch.fetch_amap` 与 `fetch_baidu`。新增的 `cancel_check` 参数为可选参数，原有 CLI 调用继续有效。

规范化输入保持现有 `source / store / poi_id / author / rating / date / content / url / raw` 字段。数字评分字符串会被解析。AMap/Baidu 按 1 至 5 分处理，其他来源需声明 `rating_min=1`、`rating_max=5`。无效或缺失评分单独排除，评论文本仍可参与摘要。来源或 `poi_id` 不匹配的记录会被拒收。

API 搜索要求餐厅名称和城市。百度浏览器搜索在没有 `BAIDU_AK` 时还要求 `city_code`，例如杭州为 `179`。高德可使用 `330100`。搜索结果保存在数据库中，创建任务使用服务器返回的 `branch_id` 并明确确认。

平台要求登录时，先使用原有 CLI 在可见浏览器中完成验证。

```powershell
python main.py reviews --source amap --poi-id B023B09325 --headed
```

后台默认复用仓库的 `browser-profile/`，也可设置 `DINELENS_BROWSER_PROFILE`。本机验证使用 `var/validation/probe-profile`，可将该目录的绝对路径配置为 `DINELENS_BROWSER_PROFILE` 复用已完成的登录与验证。后台遇到验证、来源故障或无可用评论时进入 `needs_operator`，页面显示处理原因。每次最多处理 200 条采集结果，临时浏览器响应文件在采集结束时清理。

既有 `out/.../reviews.jsonl` 可以通过分析 CLI 处理。

```powershell
python -m backend.manage analyse out/example/reviews.jsonl --source amap --poi-id B023B09325 --store 楼外楼 --city 杭州 --output out/example/report.json
```

## 分析与评分

默认 `DINELENS_ANALYSIS_MODE=rules` 使用明确的促销模式筛查、英文 VADER 和中文领域词典。每条保留评论可包含多个方面及混合情感。摘要仅抽取实际文本，每段引用返回评论标识和 Unicode 码点位置。报告展示数量、筛查原因、评分资格、分布、原文引用、数据哈希及算法版本。

设置以下变量可以使用本地 Ollama 模型。模型须事先安装，模型名可替换为团队实际选定的名称。

```powershell
$env:DINELENS_ANALYSIS_MODE='ollama'
$env:DINELENS_OLLAMA_MODEL='qwen2.5:3b'
$env:DINELENS_OLLAMA_URL='http://127.0.0.1:11434'
```

两种模式复用明确促销模式的保守筛查。Ollama 负责保留评论的方面与情感，使用 `/api/chat` 返回 JSON Schema 结构化结果。模型返回的引用编号通过 schema 枚举限制为候选原文片段，后端按编号还原引用并逐字校验。格式或引用错误会提示模型修正一次，再次失败时任务进入失败状态。单次调用超时为 60 秒，取消在当前评论处理后的检查点生效。[Ollama 接口说明](https://docs.ollama.com/api/chat)

本机验证使用项目内的 Windows 便携运行时及模型目录，服务监听 `127.0.0.1:11435`。RTX 4060 通过 Vulkan 执行推理。重新启动该运行时可使用以下命令，随后在另一个终端设置上述 DineLens 变量，将 URL 改为 `http://127.0.0.1:11435`。

```powershell
$env:OLLAMA_HOST='127.0.0.1:11435'
$env:OLLAMA_MODELS=(Join-Path (Get-Location) 'var\ollama-models')
$env:OLLAMA_NO_CLOUD='1'
$env:OLLAMA_VULKAN='1'
$env:OLLAMA_CONTEXT_LENGTH='4096'
.\var\ollama-runtime\bin\ollama.exe serve
```

其他机器可下载官方 Windows 便携 ZIP 并解压至 `var/ollama-runtime/bin`，或使用已安装的 Ollama。在服务启动后，使用同一 `OLLAMA_HOST` 执行 `ollama pull qwen2.5:3b`。项目运行数据由 `.gitignore` 排除。[Windows 运行说明](https://docs.ollama.com/windows)

默认评分方法 `bayesian_time_v1` 使用 3.5 的参考均值、10 的参考强度及 180 天时间半衰期。无日期记录权重为 1，未来日期年龄按 0 处理。报告返回这些参数、原始均值、有效数量和时间权重总量。`DINELENS_SCORE_METHOD` 还支持 `mean_v1` 和 `bayesian_v1`，可用于对照评估。参数为当前实现默认值，仍需团队用开发数据校准。

有效数字评分为 0 时综合分为 `null`，1 至 4 条提示样本少，至少 5 条标为 `adequate`。报告还单独给出已分类正负面评论中负面比例的 95% Wilson 区间。混合及未知情感单独保留。

## 会话与权限

匿名用户先调用 `POST /owner-session`。服务器设置 HttpOnly、SameSite Cookie，并返回 CSRF token。所有者写操作带 `Origin` 和 `X-CSRF-Token`，私有资源按 Cookie 中的随机凭证隔离。数据库保存凭证哈希。

`web/api.js` 自动处理会话和 CSRF。原有任务列表页面已接入该客户端。页面可从本地 `/web/` 或 Cloudflare Pages 打开。已有 API 客户端需要增加匿名会话与 CSRF 交接。

清除浏览器数据会丢失本地凭证，服务器记录和既有分享按各自规则保留。主动删除历史会立即关闭报告访问、删除其原文证据并撤销关联分享。跨设备找回匿名凭证尚无产品入口。

生产环境将 `DINELENS_ORIGINS` 设置为实际 Pages HTTPS 域名，并设置 `DINELENS_COOKIE_SECURE=true`。`DINELENS_WEB_URL` 可明确指定分享页面所在的根 URL。代理继续将浏览器的 `/api/jobs` 转到后端 `/jobs`。

## API

| 方法 | 路径 | 成功状态 | 用途 |
| --- | --- | --- | --- |
| GET | `/` | 200 | 健康检查 |
| POST GET | `/owner-session` | 201 或 200 | 建立或读取匿名会话 |
| GET | `/branches?q=名称&city=城市&source=amap` | 200 | 搜索分店，可附 `city_code` |
| POST | `/jobs` | 201 | 创建任务，可带 `Idempotency-Key` |
| GET | `/jobs` | 200 | 当前所有者的任务列表 |
| GET | `/jobs/{id}` | 200 | 查询任务与已有结果 |
| POST | `/jobs/{id}/cancel` | 200 | 请求取消 |
| GET | `/reports/{id}` | 200 | 所有者读取报告 |
| GET | `/history?limit=20&offset=0` | 200 | 历史列表和 `next_offset` |
| GET | `/notifications?limit=20&offset=0` | 200 | 报告通知、未读数和 `next_offset` |
| POST | `/notifications/{id}/read` | 204 | 幂等标记已读 |
| DELETE | `/history/{id}` | 204 | 删除历史并关闭访问 |
| POST GET | `/reports/{id}/shares` | 201 或 200 | 创建或列出分享 |
| DELETE | `/shares/{id}` | 204 | 撤销分享 |
| GET | `/shared-report` | 200 | 用 Bearer 分享令牌只读查看 |

创建任务示例包含 `store`、`branch_id` 和 `branch_confirmed=true`。仅含 `store` 的旧请求仍可创建任务，随后因缺少已确认分店进入 `needs_operator`。响应继续保留 `id/store/state/cancel_requested/progress/error/report/created_at/started_at/finished_at`，并增加 `branch` 和 `report_id`。

幂等键在同一匿名所有者下唯一，相同内容返回同一任务，不同内容返回 409。键保留至任务元数据清理。`Location` 返回相对路径 `jobs/{id}`，适配直连和 Pages 前缀。状态轮询只读取结果。

分享创建接口返回绝对 `share_url`、`share_id` 和到期时间。令牌位于 URL fragment，页面通过 Authorization 头读取报告。分享列表只返回元数据。公共响应采用字段白名单，筛查明细与所有者管理信息保留在私有接口。

分析页面每 10 秒轮询站内报告通知，切回页面时立即刷新。通知按匿名所有者隔离，支持已读状态和分页。读取通知时从已提交报告补齐缺失事件，每份报告唯一一条。通知写入在独立事务中执行，失败后下一次轮询重试，已完成的报告保持可读。删除历史会同时移除通知，报告过期后通知停止展示。

常见错误为 401 会话缺失或过期、403 来源或 CSRF 校验失败、404 资源不可访问、409 幂等或终态冲突、422 输入无效、429 会话创建频率过高、503 搜索来源不可用。

## 持久化 取消与维护

SQLite 文件默认位于 `var/dinelens.sqlite3`。迁移版本记录在 `schema_versions`，初始结构位于 `backend/migrations/001_initial.sql`，版本 2 的通知结构位于 `002_notifications.sql`。旧数据库启动时自动升级，已有报告会在首次通知轮询时补齐事件。

任务、报告及历史通过同一事务提交，原文证据独立存储。取消与结果提交由现有 manager 锁串行化。进程重启后排队及未完成任务重新排队，已收集且未过期的检查点可复用，完成报告保持可读。单 worker 进程锁防止多实例写入同一数据库。

默认期限为分享 30 天、原文与检查点 90 天、报告 365 天，可分别配置 `DINELENS_SHARE_DAYS`、`DINELENS_RAW_DAYS`、`DINELENS_REPORT_DAYS`。这是本版实现配置，最终期限由团队确认。服务启动、任务完成以及空闲时每分钟执行清理，也可手动运行。

```powershell
python -m backend.manage cleanup
```

运营者修正来源或模型问题后，停止服务，再将失败或待人工处理的任务重新排队，随后重启服务。操作通过本机命令行权限执行，原因写入任务记录。

```powershell
python -m backend.manage retry JOB_ID --reason "已完成来源验证"
```

## 验证

```powershell
python -m pytest -q
$env:DINELENS_BROWSER_TESTS='1'
$env:DINELENS_BROWSER_CHANNEL='msedge'
python -m pytest backend/test_browser.py -q
```

浏览器测试使用真实页面、API、分析和数据库，并在采集边界注入固定样本。可选择 `chrome` 或安装 Playwright Chromium 后调整测试启动通道。测试、算法指标及真实来源检查记录位于 `docs/personal_delivery.md` 和 `docs/validation/`。
