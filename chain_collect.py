"""全量采集自动链路（后台运行）。

监控已触发的「最大 batch1」(run 33054942763)，随后顺序触发：
  最大 b2 (3001-6116) -> 爱奇艺 b1/b2 -> 魔都 b1/b2
每个批次经 GitHub Actions 跑：采集 -> 生成分片 -> 部署 qinjin+share -> 提交 media.db。
本脚本只负责「触发 + 等待 + 核验」，不修改任何代码。
核验方式：每批成功后用 `git show origin/main:data/media.db` 取 CI 提交的库，
统计各分类唯一片数（按 name+year 去重），写入 chain.log。
"""
import urllib.request, json, time, subprocess, sqlite3, os, datetime

PAT = os.environ.get("GITHUB_PAT", "")
if not PAT:
    raise SystemExit("请先设置环境变量 GITHUB_PAT（GitHub PAT，actions:write）")
PROXY = "http://127.0.0.1:10808"
REPO = "a313341127/m3u-library"
WF = "update.yml"
HDR = {
    "Authorization": "Bearer " + PAT,
    "Accept": "application/vnd.github+json",
    "User-Agent": "curl/8.8",
    "Content-Type": "application/json",
}

_op = urllib.request.build_opener(urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))


def api(method, url, data=None):
    """GET（或任何带响应体的请求）。带重试，解析 JSON。"""
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    for k, v in HDR.items():
        req.add_header(k, v)
    last = None
    for _ in range(4):
        try:
            return json.load(_op.open(req, timeout=30))
        except Exception as e:
            last = e
            time.sleep(4)
    raise RuntimeError("API %s failed: %r" % (url, last))


def api_post(url, data):
    """POST 触发类请求（如 workflow_dispatch）。GitHub 成功返回 204 No Content（空响应体），
    绝不能 json.loads 空体，也不能重试（重试会重复触发 side effect，造成多个 run）。
    这里只发一次，按状态码判定成功。"""
    body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    for k, v in HDR.items():
        req.add_header(k, v)
    try:
        resp = _op.open(req, timeout=30)
        code = resp.status
        resp.read()  # 读空，避免连接悬挂
    except urllib.error.HTTPError as e:
        code = e.code
        try:
            e.read()
        except Exception:
            pass
    if 200 <= code <= 299:
        return code
    raise RuntimeError("POST %s 返回 %s" % (url, code))


def parse_iso(s):
    return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)


def dispatch(source, start, chunk):
    t0 = datetime.datetime.now(datetime.timezone.utc)
    url = "https://api.github.com/repos/%s/actions/workflows/%s/dispatches" % (REPO, WF)
    api_post(url, {"ref": "main", "inputs": {
        "MODE": "full", "SOURCES": source,
        "PAGE_START": str(start), "PAGE_CHUNK": str(chunk)}})
    # 找到刚触发的 run
    for _ in range(20):
        runs = api("GET", "https://api.github.com/repos/%s/actions/runs?per_page=5" % REPO)
        for r in runs.get("workflow_runs", []):
            ct = parse_iso(r["created_at"])
            if ct >= t0 - datetime.timedelta(seconds=30):
                return r["id"]
        time.sleep(5)
    raise RuntimeError("找不到刚触发的 run")


def wait_run(run_id, label, timeout_h=7.5):
    deadline = time.time() + timeout_h * 3600
    while time.time() < deadline:
        r = api("GET", "https://api.github.com/repos/%s/actions/runs/%s" % (REPO, run_id))
        st, con = r["status"], r.get("conclusion")
        if st == "completed":
            LOG("%s: completed (%s)" % (label, con))
            return con
        if st in ("cancelled", "failure"):
            LOG("%s: %s/%s" % (label, st, con))
            return con
        time.sleep(150)
    LOG("%s: TIMEOUT waiting (likely 6h job limit)" % label)
    return None


def unique_counts():
    # 取 CI 提交的 media.db（不动本地工作树）
    p = "/tmp/chain_media.db"
    try:
        subprocess.run(["git", "fetch", "origin", "main"], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with open(p, "wb") as f:
            f.write(subprocess.check_output(["git", "show", "origin/main:data/media.db"]))
    except Exception as e:
        LOG("  pull media.db failed: %r" % e)
        return None
    c = sqlite3.connect(p)
    out = {}
    for cat in ("movie", "tv", "anime", "variety"):
        rows = c.execute("SELECT name,year FROM resources WHERE category=?", (cat,)).fetchall()
        out[cat] = len(set(((r[0] or "").strip().lower(), r[1]) for r in rows))
    out["live"] = c.execute("SELECT count(*) FROM live").fetchone()[0]
    out["rows"] = c.execute("SELECT count(*) FROM resources").fetchone()[0]
    c.close()
    return out


LOG_F = open("chain.log", "a", encoding="utf-8")


def LOG(msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    LOG_F.write(line + "\n")
    LOG_F.flush()


# (source, run_id_or_None, start, chunk)
BATCHES = [
    ("最大", 33054942763, None, None),   # 已触发，仅监控
    ("最大", None, 3001, 3000),
    ("爱奇艺", None, 1, 3000),
    ("爱奇艺", None, 3001, 330),
    ("魔都", None, 1, 3000),
    ("魔都", None, 3001, 1385),
]

LOG("CHAIN START: %d batches" % len(BATCHES))
for i, (source, run_id, start, chunk) in enumerate(BATCHES):
    if run_id:
        label = "%s run %s (监控)" % (source, run_id)
        LOG("[BATCH %d/%d] %s" % (i + 1, len(BATCHES), label))
        con = wait_run(run_id, label)
    else:
        label = "%s %d-%d" % (source, start, start + chunk - 1)
        LOG("[BATCH %d/%d] dispatch %s" % (i + 1, len(BATCHES), label))
        try:
            rid = dispatch(source, start, chunk)
            LOG("  -> run %s" % rid)
            con = wait_run(rid, label)
        except Exception as e:
            LOG("  dispatch error: %r" % e)
            con = None
    if con == "success":
        try:
            cnt = unique_counts()
            LOG("  唯一片数: %s" % cnt)
        except Exception as e:
            LOG("  count error: %r" % e)
    else:
        LOG("  !! 本批未成功: %s（继续下一批）" % con)

LOG("CHAIN DONE")
try:
    LOG("FINAL 唯一片数: %s" % unique_counts())
except Exception as e:
    LOG("final count error: %r" % e)
LOG_F.close()
