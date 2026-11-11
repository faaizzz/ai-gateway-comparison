"""Create the virtual key the prod profile benchmarks with.

Runs inside the stack (service litellm-setup) after the proxy is healthy.
Calls POST /key/generate with the master key: a fixed, dummy key value (so k6
can use it without reading anything back), restricted to the mock-gpt alias and
given a budget. Spend tracking charges every request to this key. Then reads the
key back with /key/info and fails if the budget is not there. On success it
writes READY_FILE (the container healthcheck looks for it) and then idles, because
`docker compose up --wait` counts an exited container as a failure.

Environment: LITELLM_URL, LITELLM_MASTER_KEY, VIRTUAL_KEY, KEY_MAX_BUDGET (USD),
READY_FILE.
Standard library only.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

URL = os.environ["LITELLM_URL"].rstrip("/")
MASTER = os.environ["LITELLM_MASTER_KEY"]
KEY = os.environ["VIRTUAL_KEY"]
BUDGET = float(os.environ.get("KEY_MAX_BUDGET", "1000000"))
READY_FILE = os.environ.get("READY_FILE", "/tmp/key-ready")


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        URL + path,
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {MASTER}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def main():
    last = None
    for _ in range(30):
        try:
            call(
                "POST",
                "/key/generate",
                {
                    "key": KEY,
                    "key_alias": "gwbench-virtual",
                    "models": ["mock-gpt"],
                    "max_budget": BUDGET,
                    "budget_duration": "30d",
                },
            )
            break
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            if e.code == 400 and "already exists" in detail.lower():
                break  # a re-run is fine
            last = f"HTTP {e.code}: {detail[:300]}"
        except (urllib.error.URLError, OSError) as e:
            last = str(e)
        time.sleep(2)
    else:
        print(f"setup-key: could not create the key: {last}", file=sys.stderr)
        return 1

    info = call("GET", f"/key/info?key={KEY}").get("info", {})
    if info.get("max_budget") != BUDGET:
        print(f"setup-key: key has max_budget {info.get('max_budget')}, expected {BUDGET}", file=sys.stderr)
        return 1
    print(f"setup-key: virtual key ready, max_budget={info['max_budget']}, spend={info.get('spend')}", flush=True)
    with open(READY_FILE, "w") as f:
        f.write("ok\n")
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    sys.exit(main())
