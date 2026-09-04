#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""云端采集监控 + 异常自动修复 (collect-monitor)。

在 GitHub Actions 云端运行 (collect-monitor.yml, 每 30 分钟)，与用户电脑开关机无关。
监控三条采集流水线 (fast-collect / update / backfill-episodes) 的健康度并自动修复：

  - 最新 run 失败/取消          -> 自动重投 (带冷却 + 重试上限, 防烧 Actions 额度)
  - run 卡死(in_progress 超时)  -> 取消 + 重投
  - 全局长时间无任何成功采集     -> 兜底重投 fast-collect (安全网)

进度读取 data/fast_collect_progress.json，写入 data/collect_status.json (云端快照)。
连续失败超上限 -> 开 Issue 升级人工。

不依赖用户电脑，所有操作走 GitHub API + GITHUB_TOKEN。
提交仅触碰 data/ 下状态文件，不命中 deploy.yml 的 paths 过滤，不会触发额外部署。
"""
import os
import json
import time
import subprocess
import urllib.request
import urllib.error
from datetime import datetime, timezone

REPO = "a313341127/m3u-library"
TOKEN = os.environ["GITHUB_TOKEN"]
API = "https://api.github.com"

WATCH_WF = ["fast-collect.yml", "update.yml", "backfill-episodes.yml"]
PRIMARY = "fast-collect.yml"          # 兜底安全网工作流

COOLDOWN_HOURS = 2                     # 同一工作流两次自动重投最小间隔 (防烧 Actions 额度)
MAX_FAILS = 4                          # 单工作流连续失败次数上限 -> 开 Issue
STALL_HOURS = 3                        # 全局无任何成功采集的停滞判定阈值
STUCK_HOURS = 1                         # run 卡死判定: in_progress 且 updated_at 长期不前进 (>1h) 即视为卡死

PROGRESS_FILE = "data/fast_collect_progress.json"
STATUS_FILE = "data/collect_status.json"
STATE_FILE = "data/collect_monitor_state.json"


def api(path, method="GET", data=None):
    req = urllib.request.Request(
        API + path, method=method,
        data=(json.dumps(data).encode() if data is not None else None),
    )
    req.add_header("Authorization", "Bearer " + TOKEN)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "collect-monitor")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            if r.status == 204:
                return None
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code in (404, 204):
            return None
        print("API ERROR", e.code, path, e.read().decode(errors="ignore")[:300])
        raise
    except Exception as e:
        print("API EXC", path, str(e)[:200])
        raise


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"last_dispatch": {}, "fails": {}, "last_health": True}


def git_commit(files, msg):
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
    subprocess.run(["git", "config", "user.email",
                    "41898282+github-actions[bot]@users.noreply.github.com"], check=True)
    subprocess.run(["git", "add"] + files, check=True)
    if subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode == 0:
        return False
    subprocess.run(["git", "commit", "-m", msg], check=True)
    for _ in range(3):
        try:
            subprocess.run(["git", "pull", "--rebase", "origin", "main"], check=True)
            subprocess.run(["git", "push", "origin", "HEAD:main"], check=True)
            return True
        except subprocess.CalledProcessError:
            time.sleep(5)
    print("WARN: failed to push state (retry next run)")
    return False


def latest_run(wf):
    d = api("/repos/%s/actions/workflows/%s/runs?per_page=15" % (REPO, wf))
    if not d:
        return None
    runs = d.get("workflow_runs", [])
    return runs[0] if runs else None


def find_stuck_run(wf, stuck_hours):
    """扫描近期 run, 找出占用并发组、卡死(updated_at 长期不前进)的 in_progress run。

    关键: 不只看最新 run。并发组被一个卡死 run 长期占用时, 更新的 run 会被 GitHub
    排队/取消, 导致最新 run 是 cancelled 而非卡死 run 本身。本函数直接扫描所有近期
    run, 命中「in_progress 且 updated_at 几乎停在创建时刻(从未推进)且已存在 > stuck_hours」
    的 run = 真卡死。健康 run 的 updated_at 会随步骤推进, 不会命中。
    """
    d = api("/repos/%s/actions/workflows/%s/runs?per_page=15" % (REPO, wf))
    if not d:
        return None
    now = datetime.now(timezone.utc)
    for r in d.get("workflow_runs", []):
        if r.get("status") != "in_progress":
            continue
        upd = r.get("updated_at"); cre = r.get("created_at")
        if not upd or not cre:
            continue
        try:
            upd_age = (now - datetime.fromisoformat(upd.replace("Z", "+00:00"))).total_seconds() / 3600.0
            cre_age = (now - datetime.fromisoformat(cre.replace("Z", "+00:00"))).total_seconds() / 3600.0
        except Exception:
            continue
        # 卡死 = in_progress 且 updated_at 长期不前进(几乎停在创建时刻), 且已存在 > stuck_hours
        if upd_age > stuck_hours and (upd_age - cre_age) < 0.1:
            return r
    return None


def latest_success_ts():
    """所有监控工作流中, 最近一次成功 run 的 created_at (ISO)。"""
    best = None
    for wf in WATCH_WF:
        d = api("/repos/%s/actions/workflows/%s/runs?per_page=20&status=success"
                % (REPO, wf))
        if not d:
            continue
        runs = d.get("workflow_runs", [])
        if runs:
            ts = runs[0]["created_at"]   # 列表按 created_at 降序
            if best is None or ts > best:
                best = ts
    return best


def redispatch(wf, inputs=None):
    t0 = datetime.now(timezone.utc)
    api("/repos/%s/actions/workflows/%s/dispatches" % (REPO, wf),
        method="POST", data={"ref": "main", "inputs": inputs or {}})
    time.sleep(4)
    d = api("/repos/%s/actions/workflows/%s/runs?per_page=10" % (REPO, wf))
    if d:
        for r in d.get("workflow_runs", []):
            if datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")) > t0:
                return r["id"]
    return None


def cancel(run_id):
    api("/repos/%s/actions/runs/%s/cancel" % (REPO, run_id), method="POST")
    print("  -> cancelled stuck run", run_id)


def open_issue(title, body):
    existing = api("/repos/%s/issues?labels=monitor&state=open&per_page=10" % REPO) or []
    if any(i["title"] == title for i in existing):
        print("  -> issue already open, skip")
        return
    api("/repos/%s/issues" % REPO, method="POST",
        data={"title": title, "body": body, "labels": ["monitor"]})
    print("  -> opened issue:", title)


def age_hours(iso):
    if not iso:
        return 1e9
    t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return (datetime.now(timezone.utc) - t).total_seconds() / 3600.0


def maybe_redispatch(state, wf, now_iso, actions, force_cooldown=None):
    last = state["last_dispatch"].get(wf)
    cd = force_cooldown if force_cooldown is not None else COOLDOWN_HOURS
    if last:
        last_t = datetime.fromisoformat(last)
        delta = (datetime.now(timezone.utc) - last_t).total_seconds() / 3600.0
        if delta < cd:
            actions.append("  -> 冷却中(距上次重投 %.1fh < %.1fh), 跳过" % (delta, cd))
            return
    new_id = redispatch(wf)
    state["last_dispatch"][wf] = now_iso
    actions.append("  -> 已重投 %s -> run %s" % (wf, new_id))


def read_progress():
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def main():
    state = load_state()
    state.setdefault("last_dispatch", {})
    state.setdefault("fails", {})
    state.setdefault("last_health", True)
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    actions = []

    # 1) 逐工作流检查最新 run
    for wf in WATCH_WF:
        # 1a) 卡死检测: 扫描近期 run, 找占用并发组、updated_at 长期不前进的 in_progress run
        blocked = find_stuck_run(wf, STUCK_HOURS)
        if blocked:
            actions.append("[%s] 卡死阻塞 run %s (created %s, updated %s, >%sh 无进展) 取消+重投"
                           % (wf, blocked["id"], blocked.get("created_at"),
                              blocked.get("updated_at"), STUCK_HOURS))
            cancel(blocked["id"])
            maybe_redispatch(state, wf, now_iso, actions)
            continue
        run = latest_run(wf)
        if not run:
            actions.append("[%s] 无历史 run" % wf)
            continue
        rid = run["id"]; status = run["status"]; concl = run.get("conclusion")
        actions.append("[%s] 最新 run %s: %s/%s (updated %s)"
                       % (wf, rid, status, concl, run.get("updated_at")))
        if status in ("queued", "in_progress", "pending", "requested", "waiting"):
            if status == "in_progress" and age_hours(run.get("updated_at")) > STUCK_HOURS:
                actions.append("  -> 卡死(>%sh) 取消+重投" % STUCK_HOURS)
                cancel(rid)
                maybe_redispatch(state, wf, now_iso, actions)
            else:
                actions.append("  -> 运行中, 观察")
            continue
        if status == "completed" and concl == "success":
            state["fails"][wf] = 0
            actions.append("  -> 成功, 重置失败计数")
            continue
        # 终态非成功 -> 失败
        state["fails"][wf] = state["fails"].get(wf, 0) + 1
        if state["fails"][wf] >= MAX_FAILS:
            actions.append("  -> 连续失败 %d 次, 开 Issue 升级" % state["fails"][wf])
            open_issue(
                "[monitor] %s 采集连续失败，需人工介入" % wf,
                "工作流 %s 的采集连续 %d 次失败/超时，已超重试上限。\n"
                "请检查源站/部署凭据后手动重投。\n最新 run: https://github.com/%s/actions/runs/%s"
                % (wf, state["fails"][wf], REPO, rid),
            )
            continue
        actions.append("  -> 失败(%d/%d) 尝试自动重投" % (state["fails"][wf], MAX_FAILS))
        maybe_redispatch(state, wf, now_iso, actions)

    # 2) 全局停滞安全网
    last_ok = latest_success_ts()
    stalled = (last_ok is None) or (age_hours(last_ok) > STALL_HOURS)
    if last_ok:
        actions.append("最近成功采集: %s (%.1fh 前)" % (last_ok, age_hours(last_ok)))
    else:
        actions.append("!! 无任何成功采集记录")
    if stalled:
        actions.append("!! 全局停滞 >%sh, 兜底重投 %s" % (STALL_HOURS, PRIMARY))
        maybe_redispatch(state, PRIMARY, now_iso, actions, force_cooldown=0.5)

    # 3) 健康判定 + 进度快照
    healthy = (not stalled) and all(
        state["fails"].get(w, 0) < MAX_FAILS for w in WATCH_WF)
    prog = read_progress()
    status_out = {
        "checked_at": now_iso,
        "healthy": healthy,
        "stalled": stalled,
        "last_success_collect": last_ok,
        "fails": state["fails"],
        "progress_pages": prog,
        "actions": actions,
    }

    # 仅状态文件必须跨 run 持久; 进度/健康变化时才额外提交 status 快照, 避免每 30min 刷提交
    to_commit = [STATE_FILE]
    write_status = False
    if state.get("last_health") != healthy:
        write_status = True
        to_commit.append(STATUS_FILE)
    state["last_health"] = healthy
    os.makedirs(os.path.dirname(STATE_FILE) or ".", exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    if write_status:
        with open(STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump(status_out, f, ensure_ascii=False, indent=2)
    git_commit(to_commit, "chore(monitor): update state/status (%s)"
               % ("healthy" if healthy else "unhealthy"))

    # 4) 输出到日志 (云端可看)
    print("=" * 60)
    print("采集监控 @", now_iso)
    print("healthy:", healthy, "| stalled:", stalled, "| last_ok:", last_ok)
    print("progress pages:", json.dumps(prog, ensure_ascii=False)[:500])
    for a in actions:
        print(a)
    print("=" * 60)


if __name__ == "__main__":
    main()
