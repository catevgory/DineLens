"""Read-only tunnel/deploy adapters around the existing tools/tunnel.py."""
from __future__ import annotations


def _core():
    from tools import tunnel as core
    return core


def status() -> dict:
    try:
        core = _core()
        pids = core.find_tunnel_pids()
        state = core.load_state()
        running = bool(pids) or core.is_tunnel_pid_alive(state.get("last_pid"))
        if not pids and running and state.get("last_pid"):
            pids = [state["last_pid"]]
        name, profile = core.active_profile()
        port = profile.get("port") if profile else None
        return {
            "available": True,
            "running": running,
            "pids": pids,
            "profile": name,
            "hostname": (profile or {}).get("hostname", ""),
            "port": port,
            "listening": bool(port and core.port_listening(port)),
            "public_url": core.public_url() or "",
            "cloudflared": core.resolve_cloudflared() or "",
            "error": None,
        }
    except Exception as exc:
        return {"available": False, "running": False, "pids": [], "profile": None,
                "hostname": "", "port": None, "listening": False,
                "public_url": "", "cloudflared": "", "error": str(exc)}


def test_public() -> dict:
    core = _core()
    url = core.public_url()
    if not url:
        return {"url": "", "ok": False, "error": "未配置公网域名"}
    code, elapsed, headers = core.http_check(url, timeout=10)
    return {
        "url": url,
        "ok": bool(code and 200 <= code < 500),
        "status": code,
        "seconds": round(elapsed, 3),
        "edge": headers.get("cf-ray", ""),
        "ip": headers.get("remote_ip", ""),
        "error": None if code else "请求失败",
    }


def deploy() -> dict:
    try:
        core = _core()
        cfg = core.load_deploy()
        return {"pages_url": cfg.get("pages_url", ""), "branch": cfg.get("branch", ""),
                "repo_dir": cfg.get("repo_dir", ""), "error": None}
    except Exception as exc:
        return {"pages_url": "", "branch": "", "repo_dir": "", "error": str(exc)}
