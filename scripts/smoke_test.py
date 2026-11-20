"""
End-to-end smoke test against a running deployment (docker compose or production).
Stdlib only, so it runs anywhere:

    python scripts/smoke_test.py --api http://localhost:8000 --mlflow http://localhost:5000 \
        --email ci-admin@example.com --expect-source mlflow

Creates (or reuses) an account, checks the served model came from the MLflow registry,
runs a prediction and checks the frontend responds. Exits non-zero on the first failure.
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

SEQUENCE = (
    "ATGGCAAGCCTACGATTCCTAGGCCTTCTAACCCTAACCATCCTCATCACAGCCATACTGCTAGCTCTTCACTACACCGC"
    "CGACACAACAACCGCCTTTTCATCCATCGCCCACATCTGCCGCGACGTTAACTACGGCTGACTGATCCG"
)


def request(method, url, body=None, token=None, form=False):
    headers = {}
    data = None
    if body is not None:
        if form:
            data = urllib.parse.urlencode(body).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw and resp.headers.get_content_type() == "application/json" else raw
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")


def check(name, condition, detail=""):
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" -> {detail}" if detail else ""))
    if not condition:
        sys.exit(1)


def wait_for(url, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(3)
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--mlflow", default="", help="MLflow server to check the champion alias on")
    parser.add_argument("--frontend", default="", help="Frontend URL to check")
    parser.add_argument("--model-name", default="marine-edna-classifier")
    parser.add_argument("--email", default="smoke-test@example.com")
    parser.add_argument("--password", default="Sm0ke-test-passw0rd")
    parser.add_argument("--expect-source", choices=["mlflow", "local"], default=None)
    parser.add_argument("--wait", type=int, default=300, help="seconds to wait for the API to become healthy")
    args = parser.parse_args()
    api = args.api.rstrip("/")

    check("API becomes healthy", wait_for(f"{api}/health", args.wait), f"{api}/health")
    status, health = request("GET", f"{api}/health")
    check("model and database ready", status == 200 and health.get("model_loaded") and health.get("database") == "ok", health)

    if args.mlflow:
        mlflow = args.mlflow.rstrip("/")
        query = urllib.parse.urlencode({"name": args.model_name, "alias": "champion"})
        status, body = request("GET", f"{mlflow}/api/2.0/mlflow/registered-models/alias?{query}")
        version = body.get("model_version", {}) if isinstance(body, dict) else {}
        check("MLflow has a @champion version", status == 200 and version.get("status") == "READY",
              f"{args.model_name} v{version.get('version')}")

    username = "smoke_" + args.email.split("@")[0].replace(".", "_").replace("-", "_")[:20]
    status, _ = request("POST", f"{api}/auth/register", {
        "username": username, "email": args.email, "full_name": "Smoke Test", "password": args.password,
    })
    check("register (or account exists)", status in (201, 409), f"HTTP {status}")
    status, tokens = request("POST", f"{api}/auth/token", {"username": args.email, "password": args.password}, form=True)
    check("login", status == 200 and "access_token" in tokens, f"HTTP {status}")
    token = tokens["access_token"]

    status, serving = request("GET", f"{api}/api/model/serving", token=token)
    check("serving info", status == 200, f"{serving.get('uri')} v{serving.get('version')}")
    if args.expect_source:
        check(f"model served from {args.expect_source}", serving.get("source") == args.expect_source, serving.get("source"))

    status, prediction = request("POST", f"{api}/api/predict", {"sequence": SEQUENCE, "use_cache": False}, token=token)
    check("prediction", status == 200 and 0 <= prediction.get("confidence", -1) <= 1,
          f"{prediction.get('predicted_class')} ({prediction.get('confidence', 0):.3f})")

    status, history = request("GET", f"{api}/me/predictions", token=token)
    check("prediction saved to history", status == 200 and history.get("total", 0) >= 1, f"{history.get('total')} items")

    if args.frontend:
        check("frontend serves the app", wait_for(f"{args.frontend.rstrip('/')}/login", 60), args.frontend)
    print("All smoke checks passed")


if __name__ == "__main__":
    main()
