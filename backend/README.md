# 最小任务与取消实现

这个目录是一个可运行的最小骨架，用来固定任务状态和三阶段取消的接口约定。采集和分析目前是模拟步骤，还没有接真实的 `fetchers` 和评分流程。

## 文件

| 文件 | 作用 |
|---|---|
| `job_manager.py` | 单 worker 线程、内存任务表、状态机、协作式取消 |
| `app.py` | FastAPI 适配层，暴露 `/jobs` 和取消接口 |
| `test_cancel.py` | 只依赖标准库的取消测试 |

## 运行

在仓库根目录：

```bash
python -m pip install -r requirements.txt
python -m uvicorn backend.app:app --reload --port 8000
```

打开：

- 接口文档：<http://127.0.0.1:8000/docs>
- 网页演示：直接打开 `web/jobs-demo.html`，或从 `web/index.html` 进入。直接打开文件时默认连接 `http://127.0.0.1:8000`。

注意：只能跑一个 uvicorn worker（默认就是 1 个）。任务表在进程内存里，多个 worker 会各自持有一份状态，互相看不到。

模拟参数可以用环境变量调整：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DINELENS_COLLECT_STEPS` | `5` | 模拟采集步数 |
| `DINELENS_ANALYSE_STEPS` | `5` | 模拟分析步数 |
| `DINELENS_STEP_DELAY` | `0.4` | 每步等待秒数 |

例如把采集拉长到 20 秒，方便手动点取消：

```bash
DINELENS_COLLECT_STEPS=50 DINELENS_STEP_DELAY=0.4 python -m uvicorn backend.app:app --port 8000
```

## API

| 方法 | 路径 | 说明 | 主要状态码 |
|---|---|---|---|
| `GET` | `/` | 健康检查 | `200` |
| `POST` | `/jobs` | 创建任务，请求体 `{"store": "..."}` | `201`, `422` |
| `GET` | `/jobs` | 任务列表 | `200` |
| `GET` | `/jobs/{id}` | 单个任务 | `200`, `404` |
| `POST` | `/jobs/{id}/cancel` | 请求取消 | `200`, `404`, `409` |

`POST /jobs/{id}/cancel` 的约定：

- `queued`：立即变成 `cancelled`，返回 `200`；
- `collecting` / `analysing`：设置 `cancel_requested`，返回 `200`，worker 在下一个检查点停止；
- `cancelled`：幂等，再次取消仍返回 `200`；
- `done` / `failed` / `needs_operator`：返回 `409`，任务状态不变；
- 不存在的任务：返回 `404`。

示例：

```bash
curl -X POST http://127.0.0.1:8000/jobs \
  -H "content-type: application/json" \
  -d '{"store":"楼外楼 孤山路店"}'

curl http://127.0.0.1:8000/jobs/<job-id>

curl -X POST http://127.0.0.1:8000/jobs/<job-id>/cancel
```

## 状态与取消语义

```text
queued -> collecting -> analysing -> done
   |          |             |
   +----------+-------------+--> cancelled

collecting/analysing -> failed
collecting/analysing -> needs_operator
```

| 当前状态 | 用户取消 | 结果 |
|---|---|---|
| `queued` | 从队列逻辑上移除 | 立即 `cancelled`，不开始采集 |
| `collecting` | 设置 `cancel_requested` | 当前页/当前请求结束后停止，不进入分析 |
| `analysing` | 设置 `cancel_requested` | 当前批次结束后停止，丢弃未完成报告 |
| `done` | 拒绝 | `409`，报告保留 |
| `failed` | 拒绝 | `409`，用 retry 而不是 cancel |
| `needs_operator` | 拒绝 | `409`，由运营者处理 |
| `cancelled` | 幂等 | `200`，状态不变 |

`cancelled` 是终态。要重新分析就新建任务，不从 `cancelled` 恢复。

取消任务不生成报告，也不写历史。报告和历史由 UC2 负责写入；UC3 只读取状态和完成链接，不写任何东西。

## 接真实采集与分析的步骤

当前 `JobManager._run_job` 里有两段 `time.sleep` 的模拟循环。接真实流程时按下面的边界替换：

1. **采集**：把采集器改成接收一个 `cancel_check` 回调，在翻页、滚动、切换来源之间调用；当前页请求允许跑完，然后抛 `JobCancelled` 或返回“已取消”。
2. **分析**：在清洗、去重、筛查、分类、摘要、评分各阶段之间调用 `cancel_check`；单次模型调用不能强行中断，等它返回后检查。
3. **提交**：报告和历史必须在同一个提交点写入，并且在写之前再检查一次取消。生产环境用数据库事务或条件更新（例如 `UPDATE jobs SET state='done' WHERE id=? AND state='analysing'`）保证取消和完成只有一个赢家。
4. **不要 kill 线程或进程**。不要用 `Thread.stop`、`terminate` 之类的方式，否则容易留下半截文件、浏览器进程和锁。
5. **检查点位置**：`JobManager.check_cancel(job_id)` 是公开检查点；真实采集器/分析器可以直接调用它，捕获 `JobCancelled` 后由 worker 统一收尾。

示例接线方式：

```python
def collect(store: str, cancel_check) -> list[dict]:
    pages = []
    for page in fetch_pages(store):
        cancel_check()          # 在页与页之间检查
        pages.extend(parse(page))
    return pages


# 在 _run_job 中
reviews = collect(job.store, lambda: self.check_cancel(job.id))
```

## 测试

```bash
python backend/test_cancel.py
```

覆盖：

- 排队阶段立即取消；
- 运行中的任务不受另一个排队任务取消的影响；
- 采集阶段取消后不生成报告；
- 分析阶段取消后不生成报告；
- 已完成任务拒绝取消；
- 不存在的任务返回 `not_found`；
- 公开检查点在取消后抛出 `JobCancelled`。

## 已知限制

- 任务只存在内存里，重启后丢失；
- 只支持单进程、单 worker；
- 没有独立的 history 存储；报告目前挂在 job 快照的 `report` 字段上。接真实实现时应由 UC2 在同一个事务里写报告和历史，UC3 只读；
- 没有鉴权、限流和审计日志；
- 采集和分析是模拟的；
- CORS 目前是全开的，方便本地打开 HTML；部署时应改成具体来源；
- 生产环境应把任务持久化到 SQLite/PostgreSQL，并用数据库或消息队列做跨进程调度。

## 与 Pages 代理的关系

浏览器调用 `/api/jobs`，Cloudflare Pages Function 会去掉 `/api` 前缀，再转发到后端的 `/jobs`。所以 FastAPI 这边不要注册 `/api/jobs`，否则会变成 `/api/api/jobs`。
