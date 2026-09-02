import urllib.request, json, os, sys

PROXY = "http://127.0.0.1:10808"
TOKEN = os.environ["GH_TOKEN"]
OWNER, REPO = "a313341127", "m3u-library"
HEAD = "8e6deb6"

op = urllib.request.build_opener(urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))


def api(path, method="GET", data=None):
    url = f"https://api.github.com/repos/{OWNER}/{REPO}/actions/{path}"
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "dispatch",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(data).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    return op.open(req, timeout=30).read().decode()


# parse args: SOURCES PAGE_START PAGE_CHUNK
src = sys.argv[1] if len(sys.argv) > 1 else "最大"
pstart = sys.argv[2] if len(sys.argv) > 2 else "1"
pchunk = sys.argv[3] if len(sys.argv) > 3 else "3000"

# 1) dispatch
api(f"workflows/update.yml/dispatches", "POST",
    {"ref": "main", "inputs": {"MODE": "full", "SOURCES": src, "PAGE_START": pstart, "PAGE_CHUNK": pchunk}})
print(f"DISPATCHED full: 源={src} start={pstart} chunk={pchunk}")

# 2) fetch the created run id
import time
time.sleep(3)
runs = json.loads(api(f"runs?head_sha={HEAD}&event=workflow_dispatch&per_page=5"))
for r in runs.get("workflow_runs", []):
    if r.get("status") in ("queued", "in_progress", "requested"):
        print("RUN_ID", r["id"], r["status"], r["created_at"])
        with open("run_id.txt", "w") as f:
            f.write(str(r["id"]))
        break
else:
    print("no matching run found; recent:", [(r["id"], r["status"]) for r in runs.get("workflow_runs", [])[:3]])
