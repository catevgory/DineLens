"""Connect the existing collection functions to the analysis engine."""
from pathlib import Path
import os
import tempfile

from .analysis import AnalysisEngine
from .job_manager import JobCancelled, NeedsOperator
from .local_model import OllamaAssessor


class Collector:
    source_mode = "live"

    def __call__(self, job, cancel_check):
        if not job.branch or not job.branch.get("id") or not job.branch.get("city"):
            raise NeedsOperator("请先搜索并确认城市和具体分店，再创建分析任务。")
        from fetchers import browser_fetch
        cancel_check()
        # Browser captures are temporary. The validated comments and report
        # evidence are retained by the backend's configured database policy.
        try:
            with tempfile.TemporaryDirectory(prefix="dinelens-collect-") as output:
                common = {"out_dir": Path(output), "headed": False, "max_scroll": 10,
                          "profile_dir": os.getenv("DINELENS_BROWSER_PROFILE", str(Path("browser-profile").resolve())),
                          "cancel_check": cancel_check}
                if job.branch["source"] == "amap":
                    rows = browser_fetch.fetch_amap(job.branch["id"], **common)
                elif job.branch["source"] == "baidu":
                    common.pop("max_scroll")
                    rows = browser_fetch.fetch_baidu(job.branch["id"], max_pages=10, name=job.store,
                                                      xy=(job.branch.get("x"), job.branch.get("y")), **common)
                else:
                    raise NeedsOperator("不支持这个评论来源。")
                if not rows:
                    raise NeedsOperator("来源没有返回可用评论，请核查分店、登录状态及提取规则。")
                return rows[:200]
        except (JobCancelled, InterruptedError, NeedsOperator):
            raise
        except (Exception, SystemExit) as exc:
            cancel_check()
            raise NeedsOperator("评论来源不可用或要求人工验证，请检查采集器配置与浏览器登录状态。") from exc


def search_branches(query, city, source, city_code=None):
    from main import load_config, search
    config = load_config()
    if source == "baidu" and not config["baidu_ak"] and not city_code:
        raise NeedsOperator("百度浏览器搜索需要城市代码，例如杭州为 179，或配置 BAIDU_AK。")
    with tempfile.TemporaryDirectory(prefix="dinelens-search-") as output:
        return search(source, query, city, city_code, False, Path(output), config,
                      os.getenv("DINELENS_BROWSER_PROFILE", str(Path("browser-profile").resolve())), "auto")


def configured_engine():
    mode = os.getenv("DINELENS_ANALYSIS_MODE", "rules")
    method = os.getenv("DINELENS_SCORE_METHOD", "bayesian_time_v1")
    if mode == "rules":
        return AnalysisEngine(score_method=method)
    if mode == "ollama":
        model = os.getenv("DINELENS_OLLAMA_MODEL", "qwen2.5:3b")
        assessor = OllamaAssessor(model, os.getenv("DINELENS_OLLAMA_URL", "http://127.0.0.1:11434"))
        return AnalysisEngine(assessor, model_version="ollama:" + model + ":" + assessor.version, score_method=method)
    raise ValueError("DINELENS_ANALYSIS_MODE must be rules or ollama")
