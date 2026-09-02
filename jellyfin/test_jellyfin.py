import urllib.request, json

BASE = "https://m3u-jellyfin.a313341127.workers.dev"
PROXY = "http://127.0.0.1:10808"
op = urllib.request.build_opener(urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))


def get(path):
    req = urllib.request.Request(BASE + path, method="GET")
    req.add_header("User-Agent", "Mozilla/5.0")
    resp = op.open(req, timeout=40)
    return resp.status, resp.headers, resp.read().decode("utf-8", "ignore")


s, h, _ = get("/Users/tubo-user/Items?ParentId=view_%E7%BE%8E%E5%9B%BD&Limit=1")
d = json.loads(_ if False else get("/Users/tubo-user/Items?ParentId=view_%E7%BE%8E%E5%9B%BD&Limit=1")[2])
mid = d["Items"][0]["Id"]
print("美国首部:", mid, d["Items"][0]["Name"])

# 5. 海报：跟随 302 看最终是否图片
s, h, _ = get("/Items/%s/Images/Primary" % mid)
print("=== Images/Primary 跟随后 ===", s, "content-type:", h.get("content-type"), "final-url:", h.get("content-type"))

# 6. 播放直链
s, h, b = get("/Items/%s/PlaybackInfo" % mid)
d2 = json.loads(b)
print("=== PlaybackInfo ===", s, "sources:", len(d2.get("MediaSources", [])))
if d2.get("MediaSources"):
    print("  Path:", d2["MediaSources"][0].get("Path", "")[:90])
