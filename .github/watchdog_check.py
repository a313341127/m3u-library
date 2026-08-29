#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-healing watchdog for m3u-library backfill collection runs.

Runs on GitHub's servers (watchdog.yml, hourly). Tracks each backfill source
(爱奇艺 / 魔都) via a GitHub repo variable (WATCHDOG_STATE) holding the run id,
status and failure count -- this avoids depending on the (often null) workflow
`inputs` field. Behaviour per source:
  - success            -> done
  - in_progress/queued -> wait (unless stuck > STUCK_HOURS, then cancel+redispatch)
  - failed/cancelled   -> redispatch (preserving last inputs), up to MAX_FAILS
  - cap exceeded       -> open a GitHub Issue (label: watchdog), stop retrying
When both sources succeed, the watchdog disables itself.
"""
import os
import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

REPO = "a313341127/m3u-library"
WF = "update.yml"
TOKEN = os.environ["GITHUB_TOKEN"]
API = "https://api.github.com"
SOURCES = ["爱奇艺", "魔都"]
SEED = {"爱奇艺": "33235916715", "魔都": "33235968987"}
STATE_VAR = "WATCHDOG_STATE"
MAX_FAILS = 3
STUCK_HOURS = 8
# default redispatch params (chunked full to stay under the 6h job limit)
DEFAULT_INPUTS = {"MODE": "full", "SOURCES": None, "PAGE_START": "1",
                  "PAGE_CHUNK": "350", "SKIP_COVERS": "false"}


def api(path, method="GET", data=None):
    req = urllib.request.Request(
        API + path,
        method=method,
        data=(json.dumps(data).encode() if data is not None else None),
    )
    req.add_header("Authorization", "Bearer " + TOKEN)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "watchdog")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            if r.status == 204:
                return None
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        body = e.read().decode(errors="ignore")
        print("API ERROR", e.code, path, body[:300])
        raise


def get_var(name):
    d = api("/repos/%s/actions/variables/%s" % (REPO, name))
    return d["value"] if d else None


def set_var(name, value):
    body = {"name": name, "value": value}
    if api("/repos/%s/actions/variables/%s" % (REPO, name), method="PATCH", data=body) is None:
        # 404 -> create
        api("/repos/%s/actions/variables" % REPO, method="POST", data=body)


def load_state():
    raw = get_var(STATE_VAR)
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            pass
    # seed from known run ids, reading their current status
    state = {}
    for src in SOURCES:
        rid = SEED[src]
        st, concl = run_status(rid)
        status = "success" if (st == "completed" and concl == "success") else (
            "running" if st in ("queued", "in_progress", "pending", "requested", "waiting") else "failed"
        )
        state[src] = {"run_id": rid, "status": status, "fails": 0,
                     "last_inputs": dict(DEFAULT_INPUTS, SOURCES=src)}
    print("seeded state:", json.dumps(state, ensure_ascii=False))
    return state


def save_state(state):
    set_var(STATE_VAR, json.dumps(state, ensure_ascii=False))


def run_status(run_id):
    d = api("/repos/%s/actions/runs/%s" % (REPO, run_id))
    if not d:
        return "unknown", None
    return d["status"], d.get("conclusion")


def latest_dispatch_run(after_ts=None):
    runs = api("/repos/%s/actions/workflows/%s/runs?per_page=20" % (REPO, WF))[
        "workflow_runs"
    ]
    cands = [r for r in runs if r.get("event") == "workflow_dispatch"]
    if after_ts:
        cands = [r for r in cands
                 if datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")) > after_ts]
    if not cands:
        cands = [r for r in runs if r.get("event") == "workflow_dispatch"]
    return cands[0] if cands else None


def redispatch(inputs):
    t0 = datetime.now(timezone.utc)
    api(
        "/repos/%s/actions/workflows/%s/dispatches" % (REPO, WF),
        method="POST",
        data={"ref": "main", "inputs": inputs},
    )
    time.sleep(4)
    new = latest_dispatch_run(after_ts=t0)
    print("  -> re-dispatched, new run:", new["id"] if new else "unknown")
    return new["id"] if new else None


def cancel(run_id):
    api("/repos/%s/actions/runs/%s/cancel" % (REPO, run_id), method="POST")
    print("  -> cancelled stuck run", run_id)


def open_issue(title, body):
    existing = api(
        "/repos/%s/issues?labels=watchdog&state=open&per_page=10" % REPO
    ) or []
    if any(i["title"] == title for i in existing):
        print("  -> issue already open, skip")
        return
    api(
        "/repos/%s/issues" % REPO,
        method="POST",
        data={"title": title, "body": body, "labels": ["watchdog"]},
    )
    print("  -> opened issue:", title)


def disable_self():
    try:
        wfs = api("/repos/%s/actions/workflows" % REPO)["workflows"]
        wid = next(
            (w["id"] for w in wfs if w.get("path") == ".github/workflows/watchdog.yml"),
            None,
        )
        if wid:
            api("/repos/%s/actions/workflows/%s/disable" % (REPO, wid), method="POST")
            print("watchdog disabled (both sources succeeded)")
        else:
            print("watchdog id not found, skip disable")
    except Exception as e:
        print("disable_self failed:", e)


def age_hours(iso):
    t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return (datetime.now(timezone.utc) - t).total_seconds() / 3600.0


def main():
    state = load_state()
    all_success = True
    for src in SOURCES:
        st = state.setdefault(
            src,
            {"run_id": SEED[src], "status": "unknown", "fails": 0,
             "last_inputs": dict(DEFAULT_INPUTS, SOURCES=src)},
        )
        rid = st["run_id"]
        status, concl = run_status(rid)
        print("[%s] run %s status=%s conclusion=%s (tracked=%s fails=%d)"
              % (src, rid, status, concl, st["status"], st["fails"]))
        if status == "completed" and concl == "success":
            st["status"] = "success"
            st["fails"] = 0
            print("  -> OK")
            continue
        if status in ("queued", "in_progress", "pending", "requested", "waiting"):
            if status == "in_progress" and age_hours(
                api("/repos/%s/actions/runs/%s" % (REPO, rid))["updated_at"]
            ) > STUCK_HOURS:
                print("  -> STUCK (>%sh), cancel + redispatch" % STUCK_HOURS)
                cancel(rid)
                new_id = redispatch(st.get("last_inputs") or dict(DEFAULT_INPUTS, SOURCES=src))
                if new_id:
                    st["run_id"] = new_id
                st["status"] = "running"
            else:
                print("  -> still running, wait")
                all_success = False
            continue
        # terminal non-success
        st["fails"] = st.get("fails", 0) + 1
        if st["fails"] >= MAX_FAILS:
            print("  -> exceeded cap (%d), open issue, stop retrying" % MAX_FAILS)
            open_issue(
                "[watchdog] %s 采集连续失败，需人工介入" % src,
                "源 %s 的回填采集连续 %d 次失败/超时，已超重试上限。\n最新 run: %s\n请检查源站/部署凭据后手动重投 update.yml（MODE=full, SOURCES=%s）。"
                % (src, st["fails"],
                   "https://github.com/%s/actions/runs/%s" % (REPO, rid), src),
            )
            st["status"] = "exhausted"
            all_success = False
            continue
        print("  -> failure (%d/%d), redispatch" % (st["fails"], MAX_FAILS))
        new_id = redispatch(st.get("last_inputs") or dict(DEFAULT_INPUTS, SOURCES=src))
        if new_id:
            st["run_id"] = new_id
        st["status"] = "running"
        all_success = False
    save_state(state)
    if all_success:
        disable_self()


if __name__ == "__main__":
    main()
