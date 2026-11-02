"""Read-only model smoke/architecture comparison against a running local Compose stack.

python tests/benchmark_agents.py --suite review --modes single multi
No chat messages or transaction writes; internal diagnostic runs have isolated IDs.
Checks below are structural/evidence checks, not a substitute for semantic grading.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import time
from urllib.request import Request, build_opener, ProxyHandler
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
OPENER = build_opener(ProxyHandler({}))


def api(port, path, token=None, payload=None, internal=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if internal:
        headers["X-Internal-Service-Token"] = internal
    req = Request(f"http://127.0.0.1:{port}{path}",
        data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
    with OPENER.open(req, timeout=160) as response:
        return json.load(response)


def internal_token():
    value = os.getenv("INTERNAL_SERVICE_TOKEN")
    if value:
        return value
    for line in (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines():
        key, separator, value = line.strip().partition("=")
        if separator and key == "INTERNAL_SERVICE_TOKEN":
            return value.strip().strip('"').strip("'")
    raise RuntimeError("Set INTERNAL_SERVICE_TOKEN or create the root .env")


def login(user, role, project):
    return api(8001, "/api/v1/auth/demo-token", payload={
        "user_id": user, "role": role, "welfare_project_id": project})["access_token"]


def digest(value):
    def stable(item):
        if isinstance(item, dict):
            return {key: stable(value) for key, value in item.items() if key not in {"as_of"}}
        if isinstance(item, list):
            return [stable(value) for value in item]
        return item
    return hashlib.sha256(json.dumps(stable(value), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def snapshot(token):
    return {key: api(8001, path, token)["data"] for key, path in (
        ("points", "/api/v1/points/account"), ("orders", "/api/v1/orders"), ("products", "/api/v1/products"))}


def run_case(case, mode, secret):
    token = login(case["user"], "customer", case["project"])
    admin = login("admin_001", "admin", case["project"])
    before = snapshot(token)
    uid = uuid4().hex
    started = time.perf_counter()
    event = api(8002, "/internal/v1/agent/runs", token, payload={
        "conversation_id": f"eval_{uid}", "turn_id": f"eval_{uid}", "execution_mode": mode,
        "messages": [{"message_id": uid, "type": "text", "content": {"text": case["question"]}}]}, internal=secret)
    run_id = event["event_data"]["run_id"]
    if event["event_type"] == "run_decision_prepared":
        event = api(8002, f"/internal/v1/agent/runs/{run_id}/confirm", token, payload={}, internal=secret)
    elapsed = round((time.perf_counter() - started) * 1000)
    detail = api(8002, f"/api/v1/runs/{run_id}", admin)
    after = snapshot(token)
    content = event["event_data"].get("content", {})
    reply_type = content.get("reply_type")
    if event["event_type"] == "run_handoff_requested":
        reply_type = "REQUEST_HANDOFF"
    trace = detail["run"].get("execution_trace") or {}
    actual_tools = {call["tool_name"] for call in detail["tool_calls"] if call["success"]}
    checks = {"reply_type": reply_type == case["reply_type"],
              "required_tools": set(case["tools"]) <= actual_tools,
              "snapshot_unchanged": digest(before) == digest(after),
              "no_degradation": not trace.get("degraded"),
              "no_unexpected_action": bool(case.get("action")) or not content.get("action")}
    if mode == "multi":
        route = trace.get("route", {})
        allowed_domains = case.get("acceptable_domains", [case["domains"]])
        checks["route_domains"] = any(set(route.get("domains", [])) == set(domains) for domains in allowed_domains)
        tasks = detail.get("tasks", [])
        covered = {task["domain"] for task in tasks if task["state"] in {"SUCCESS", "PARTIAL"}}
        checks["required_domains_have_result"] = set(route.get("domains", [])) <= covered
        checks["no_running_tasks"] = all(task["state"] != "RUNNING" for task in tasks)
    if case.get("minimum_facts"):
        paths = {fact["field_path"].split(".")[-1] for fact in content.get("business_facts", [])}
        checks["fact_references"] = set(case["minimum_facts"]) <= paths
    if case.get("policy"):
        checks["policy_sources"] = bool(content.get("sources"))
    if case.get("action"):
        checks["action_href"] = content.get("action", {}).get("href") == case["action"]
    return {"case_id": case["id"], "mode": mode, "run_id": run_id, "checks": checks,
        "smoke_checks_passed": all(checks.values()), "semantic_review": "pending",
        "latency_ms": detail["run"].get("latency_ms"), "round_trip_ms": elapsed,
        "input_tokens": detail["run"]["input_tokens"], "output_tokens": detail["run"]["output_tokens"],
        "model_calls": trace.get("budget", {}).get("model_calls"), "tools": sorted(actual_tools),
        "failed_task_attempts": sum(task["state"] == "FAILED" for task in detail.get("tasks", [])),
        "snapshot_digest": digest(before), "snapshot_after_digest": digest(after),
        "event": event, "execution_trace": trace}


def git_metadata():
    args = ["git", "-c", f"safe.directory={ROOT.as_posix()}"]
    commit = subprocess.check_output([*args, "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output([*args, "status", "--porcelain"], cwd=ROOT, text=True))
    files = sorted(path for folder in ("ai-service/atguigu", "customer-service/atguigu", "knowledge/policies")
                   for path in (ROOT / folder).rglob("*") if path.is_file() and path.suffix in {".py", ".json"})
    source_digest = hashlib.sha256()
    for path in files:
        source_digest.update(path.relative_to(ROOT).as_posix().encode())
        source_digest.update(path.read_bytes())
    return {"commit": commit, "dirty": dirty, "source_digest": source_digest.hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=["review", "extended", "all"], default="review")
    parser.add_argument("--modes", nargs="+", choices=["single", "multi"], default=["single", "multi"])
    parser.add_argument("--cases", nargs="+", help="Optional case IDs")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--output", type=Path, default=ROOT / "tmp/agent-evaluation/latest.json")
    args = parser.parse_args()
    dataset = json.loads((ROOT / "tests/agent_cases.json").read_text(encoding="utf-8"))
    cases = [case for case in dataset["cases"] if (args.suite == "all" or case["suite"] == args.suite)
             and (not args.cases or case["id"] in args.cases)]
    if not cases or args.repeat < 1:
        parser.error("No selected cases or invalid repeat count")
    secret = internal_token()
    report = {"dataset_version": dataset["version"], "dataset_digest": digest(dataset), "source": git_metadata(), "rows": [],
              "scope": "AI execution only; no Turn queue/realtime latency; semantic grading pending"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for repeat in range(args.repeat):
        for case in cases:
            # Alternate order to reduce systematic warm-cache bias; record every run.
            modes = args.modes if repeat % 2 == 0 else list(reversed(args.modes))
            for mode in modes:
                try:
                    row = run_case(case, mode, secret)
                except Exception as exc:
                    row = {"case_id": case["id"], "mode": mode, "smoke_checks_passed": False,
                           "error": type(exc).__name__, "semantic_review": "pending"}
                report["rows"].append({"repeat": repeat + 1, **row})
                args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                print(json.dumps({key: row.get(key) for key in ("case_id", "mode", "smoke_checks_passed",
                    "latency_ms", "input_tokens", "output_tokens", "checks", "error")}, ensure_ascii=False), flush=True)
    for mode in args.modes:
        rows = [row for row in report["rows"] if row["mode"] == mode]
        values = [row["latency_ms"] for row in rows if row.get("latency_ms") is not None]
        report.setdefault("summary", {})[mode] = {"requests": len(rows),
            "smoke_checks_passed": sum(row["smoke_checks_passed"] for row in rows),
            "mean_latency_ms": round(statistics.mean(values)) if values else None,
            "total_tokens": sum(row.get("input_tokens", 0) + row.get("output_tokens", 0) for row in rows)}
    report["same_data_per_case"] = {case["id"]: len({row.get("snapshot_digest") for row in report["rows"]
        if row["case_id"] == case["id"] and row.get("snapshot_digest")}) == 1 for case in cases}
    report["source_after"] = git_metadata()
    report["source_unchanged"] = report["source"] == report["source_after"]
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved {args.output}")
    return 0 if all(row["smoke_checks_passed"] for row in report["rows"]) and report["source_unchanged"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
