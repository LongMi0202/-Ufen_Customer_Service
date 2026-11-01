"""Check local HTTP services and cross-service authentication without calling the LLM."""

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, ProxyHandler


opener = build_opener(ProxyHandler({}))


def request(url: str, *, token: str | None = None, payload: dict | None = None):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode()
    with opener.open(Request(url, data=data, headers=headers), timeout=10) as response:
        body = response.read().decode()
        if "application/json" in response.headers.get("Content-Type", ""):
            return json.loads(body)
        return body


def main() -> None:
    for port in (8000, 8001, 8002):
        result = request(f"http://127.0.0.1:{port}/health")
        assert result["status"] == "ok", f"Unhealthy API on {port}"
        print(f"PASS API health :{port}")

    for port in (5173, 5174):
        base = f"http://127.0.0.1:{port}"
        assert 'id="app"' in request(base), f"Missing frontend app on {port}"
        assert "createElementVNode" in request(f"{base}/src/App.vue"), (
            f"Vue compilation failed on {port}"
        )
        api_module = request(f"{base}/src/api.js")
        assert "192.168.200." not in api_module, f"Stale VM URL on {port}"
        print(f"PASS frontend HTML, Vue compilation and local API URLs :{port}")

    tokens = {}
    for user_id, role in (("user_001", "customer"), ("agent_001", "agent"), ("admin_001", "admin")):
        result = request(
            "http://127.0.0.1:8001/api/v1/auth/demo-token",
            payload={"user_id": user_id, "role": role},
        )
        assert result["principal"]["role"] == role
        tokens[role] = result["access_token"]
        print(f"PASS demo login {role}")

    checks = (
        (8001, "/api/v1/orders", "customer"),
        (8000, "/api/v1/chat/history", "customer"),
        (8000, "/api/v1/handoffs", "agent"),
        (8000, "/api/v1/admin/metrics", "admin"),
        (8001, "/api/v1/admin/summary", "admin"),
        (8002, "/api/v1/admin/metrics", "admin"),
        (8002, "/api/v1/runs", "admin"),
    )
    for port, path, role in checks:
        request(f"http://127.0.0.1:{port}{path}", token=tokens[role])
        print(f"PASS authenticated {role} :{port}{path}")
    print("All checks passed. No LLM request or business operation was performed.")


if __name__ == "__main__":
    try:
        main()
    except (HTTPError, URLError, AssertionError, KeyError) as exc:
        raise SystemExit(f"FAIL {exc}") from None
