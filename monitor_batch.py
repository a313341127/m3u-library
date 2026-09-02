import urllib.request, json, os, time

PROXY = "http://127.0.0.1:10808"
TOKEN = os.environ["GH_TOKEN"]
RUN_ID = os.environ.get("RUN_ID", "33054942763")
OP = urllib.request.build_opener(urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))


def api(p, retry=4):
    for _ in range(retry):
        try:
            req = urllib.request.Request(
                "https://api.github.com/repos/a313341127/m3u-library/actions/" + p,
                headers={"Authorization": "Bearer " + TOKEN, "Accept": "application/vnd.github+json"})
            return json.loads(OP.open(req, timeout=30).read())
        except Exception:
            time.sleep(5)
    return None


def live_all(retry=4):
    for _ in range(retry):
        try:
            req = urllib.request.Request("https://qinjin.pages.dev/api/all.json",
                                        headers={"User-Agent": "monitor"})
            return json.loads(OP.open(req, timeout=30).read())
        except Exception:
            time.sleep(5)
    return None


print("[monitor] watching run", RUN_ID, "start", time.strftime("%H:%M:%S"))
last = None
while True:
    r = api(f"runs/{RUN_ID}")
    if not r:
        time.sleep(60)
        continue
    st = r["status"]
    concl = r.get("conclusion")
    if st != last:
        print(f"[{time.strftime('%H:%M:%S')}] status={st} conclusion={concl}")
        last = st
    if st == "completed":
        print("[monitor] RUN COMPLETED conclusion=", concl)
        for _ in range(20):  # 等部署与边缘缓存刷新
            d = live_all()
            if d and "count" in d:
                print(f"[monitor] LIVE all.json total={d['count']} sharded={d.get('sharded')}")
                if d.get("cats"):
                    for c, info in d["cats"].items():
                        print(f"   {c}: {info.get('count')}")
                break
            time.sleep(15)
        else:
            print("[monitor] could not fetch live all.json")
        break
    time.sleep(150)
print("[monitor] done")
