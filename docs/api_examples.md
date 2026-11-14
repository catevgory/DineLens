# API 调用样例

后端直连基路径为 `http://127.0.0.1:8000`，Pages 使用 `https://你的域名/api`。每个请求携带同一匿名会话 Cookie。下面的 Python 示例可从已有依赖环境运行，搜索与采集需要来源可用。

```python
import json
import time
import uuid
from urllib.parse import urlsplit
import requests

base = "http://127.0.0.1:8000"
parts = urlsplit(base)
session = requests.Session()
session.headers["Origin"] = f"{parts.scheme}://{parts.netloc}"
response = session.post(base + "/owner-session", timeout=10)
response.raise_for_status()
session.headers["X-CSRF-Token"] = response.json()["csrf_token"]

response = session.get(base + "/branches", params={
    "q": input("餐厅名称 "), "city": input("城市 "), "source": "amap"
}, timeout=120)
response.raise_for_status()
branches = response.json()["items"]
for index, branch in enumerate(branches):
    print(index, branch["name"], branch["address"])
if not branches:
    raise SystemExit("未找到可确认的分店")
branch = branches[int(input("核对地址后输入分店序号 "))]

response = session.post(base + "/jobs", json={
    "store": branch["name"], "branch_id": branch["branch_id"],
    "branch_confirmed": True
}, headers={"Idempotency-Key": str(uuid.uuid4())}, timeout=10)
response.raise_for_status()
job_id = response.json()["id"]
while True:
    response = session.get(base + "/jobs/" + job_id, timeout=10)
    response.raise_for_status()
    job = response.json()
    print(job["state"], job["progress"])
    if job["state"] not in {"queued", "collecting", "analysing"}:
        break
    time.sleep(2)

if job["state"] == "done":
    report = session.get(base + "/reports/" + job["report_id"], timeout=10)
    report.raise_for_status()
    print(json.dumps(report.json(), ensure_ascii=False, indent=2))
else:
    print(job["error"] or job["state"])
```

幂等键应由调用者为每次新分析生成，同一请求重试时复用。浏览器客户端使用 `crypto.randomUUID()`。分享创建后只在该次响应中返回完整链接，可通过以下调用查看和撤销。

```python
share = session.post(base + "/reports/" + job["report_id"] + "/shares", timeout=10)
share.raise_for_status()
print(share.json()["share_url"])
share_id = share.json()["share_id"]
session.delete(base + "/shares/" + share_id, timeout=10).raise_for_status()
```

浏览器的具体实现可复用 `web/api.js`。OpenAPI 在 `/openapi.json` 中包含写接口的 `X-CSRF-Token` 和任务的 `Idempotency-Key` 参数。

报告通知复用同一会话。写请求继续携带会话建立时设置的 CSRF 和 Origin。

```python
notices = session.get(base + "/notifications?limit=20&offset=0", timeout=10)
notices.raise_for_status()
print(notices.json()["unread_count"])
for notice in notices.json()["items"]:
    print(notice["report_id"], notice["read_at"])
    session.post(base + "/notifications/" + notice["notification_id"] + "/read", timeout=10).raise_for_status()
```
