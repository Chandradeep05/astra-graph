"""
Anvaya Deployment Smoke Test Script.
Validates live HTTP API endpoints without mutating production graph data unexpectedly.

Usage:
    python scripts/smoke_test.py [--base-url http://localhost:8000]
    python scripts/smoke_test.py --base-url https://anvaya-backend.onrender.com
"""
import sys
import argparse
import json
import urllib.request
import urllib.error


def make_request(url: str, method: str = "GET", payload: dict = None, headers: dict = None):
    req_headers = {"Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    data = None
    if payload is not None:
        req_headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body) if "application/json" in resp.headers.get("Content-Type", "") else body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = body
        return exc.code, parsed
    except Exception as exc:
        return 0, str(exc)


def run_smoke_test(base_url: str):
    base_url = base_url.rstrip("/")
    print(f"\n=======================================================")
    print(f"ANVAYA DEPLOYMENT SMOKE TEST: {base_url}")
    print(f"=======================================================\n")

    passed = 0
    total = 0

    def check(title: str, condition: bool, details: str = ""):
        nonlocal passed, total
        total += 1
        status_str = "PASS" if condition else "FAIL"
        print(f"[{status_str}] #{total:02d}: {title}")
        if details and not condition:
            print(f"       Details: {details}")
        if condition:
            passed += 1

    # 1. GET /health
    code, data = make_request(f"{base_url}/health")
    check("Health Check (GET /health)", code == 200 and data.get("status") == "healthy", f"Status: {code}, Data: {data}")

    # 2. Known ARTH reviewer query
    code, data = make_request(f"{base_url}/api/v1/reviewers?repo=arth&file=render.yaml")
    check("Known ARTH Reviewers Query", code == 200 and data.get("total_candidates", 0) > 0, f"Code: {code}")

    # 3. Known databases impact query
    code, data = make_request(f"{base_url}/api/v1/impact?repo=databases&file=databases/backends/postgres.py")
    check("Known Databases Impact Query", code == 200 and len(data.get("impacted_files", [])) > 0, f"Code: {code}")

    # 4. Impact -> Reviewer combined workflow
    code, data = make_request(f"{base_url}/api/v1/impact-reviewers?repo=databases&file=databases/backends/postgres.py")
    check("Impact -> Reviewers Combined Workflow", code == 200 and len(data.get("candidates", [])) > 0, f"Code: {code}")

    # 5. Graph Explorer Overview
    code, data = make_request(f"{base_url}/api/v1/graph?repo=arth&max_nodes=10")
    check("Graph Explorer Overview", code == 200 and len(data.get("nodes", [])) > 0, f"Code: {code}")

    # 6. Evidence Path Tracing
    code, data = make_request(f"{base_url}/api/v1/graph/evidence-path?repo=arth&target_file=render.yaml&reviewer_name=chandradeep05")
    check("Evidence Path Tracing", code == 200 and data.get("path_found") is True, f"Code: {code}")

    # 7. Action Proposal Generation via Ask
    code, data = make_request(
        f"{base_url}/api/v1/ask",
        method="POST",
        payload={"message": "Who should review databases/backends/postgres.py?", "repo": "databases"},
    )
    has_action = code == 200 and data.get("action") is not None
    check("Action Proposal Generation (Ask)", has_action, f"Code: {code}, Action: {data.get('action') if isinstance(data, dict) else data}")

    # 8. Action Approval State Machine Gate (Missing Evidence Rejected)
    code, data = make_request(
        f"{base_url}/api/v1/actions",
        method="POST",
        payload={"type": "request_review", "repo": "arth", "target": "internal.py", "reviewer": "admin", "reason": "test", "evidence_ids": []},
    )
    check("Action Approval Gate (Reject Empty Evidence)", code in (400, 422), f"Code: {code}")

    # 9. Context Package
    code, data = make_request(
        f"{base_url}/api/v1/context",
        method="POST",
        payload={"repository": "arth", "subject": "render.yaml", "depth": 2},
    )
    check("Context Package Generation", code == 200 and data.get("subject") == "render.yaml", f"Code: {code}")

    # 10. Unknown File -> Structured Refusal (NO_PATH)
    code, data = make_request(
        f"{base_url}/api/v1/ask",
        method="POST",
        payload={"message": "Who modified non_existent_file_99999.xyz?", "repo": "arth"},
    )
    check("Unknown File Refusal (NO_PATH)", code == 200 and data.get("refusal") is True and data.get("reason_code") == "NO_PATH", f"Data: {data}")

    # 11. CORS Preflight Check
    code, data = make_request(
        f"{base_url}/api/v1/health",
        method="OPTIONS",
        headers={"Origin": "https://anvaya.vercel.app", "Access-Control-Request-Method": "GET"},
    )
    check("CORS Preflight Headers", code in (200, 204), f"Code: {code}")

    # 12. UI Serving Route
    code, data = make_request(f"{base_url}/", headers={"Accept": "text/html"})
    check("UI Route Serving (GET /)", code == 200 and ("<!DOCTYPE html>" in str(data) or "ANVAYA" in str(data)), f"Code: {code}")

    print(f"\n-------------------------------------------------------")
    print(f"SMOKE TEST SUMMARY: {passed}/{total} checks passed.")
    print(f"-------------------------------------------------------\n")
    return passed == total


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Anvaya deployment smoke test")
    parser.add_argument("--base-url", default="http://localhost:8000", help="Base URL of deployed API")
    args = parser.parse_args()

    success = run_smoke_test(args.base_url)
    sys.exit(0 if success else 1)
