#!/usr/bin/env python3
"""Задачи агенту: попросил с телефона — Claude Code сходил браузером и написал отчёт.

Хранилище — agent_tasks.json (данные машины, не репозитория), отчёты — agent/reports/<id>/.
Сам запуск живёт в scripts/agent_run.sh; здесь только состояние задач.

  python3 scripts/agent.py add "найди самокаты для ребёнка 1.5 года на avito и ozon"
  python3 scripts/agent.py list
  python3 scripts/agent.py show <id>
  python3 scripts/agent.py start <id>            # пометить «выполняется», напечатать рабочий каталог
  python3 scripts/agent.py prepare <id>          # + положить в каталог task.txt и mcp.json
  python3 scripts/agent.py finish <id> [--report FILE] [--error TEXT] [--cost N] [--turns N]
  python3 scripts/agent.py remove <id>
"""
import argparse, json, os, re, secrets, sys, time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE = os.path.join(ROOT, "agent_tasks.json")
REPORTS = os.path.join(ROOT, "agent", "reports")
KEEP = 60          # столько задач храним, старые уезжают вместе с отчётами
STALE_SEC = 45 * 60  # задача, «выполняющаяся» дольше этого, считается сорвавшейся


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def load():
    if not os.path.exists(STORE):
        return {"tasks": []}
    with open(STORE, encoding="utf-8") as f:
        return json.load(f)


def save(data):
    tmp = STORE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STORE)


def task_dir(tid):
    return os.path.join(REPORTS, tid)


def find(data, tid):
    for t in data["tasks"]:
        if t["id"] == tid:
            return t
    return None


def add(prompt):
    prompt = (prompt or "").strip()
    if not prompt:
        raise SystemExit("пустая задача")
    data = load()
    tid = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
    t = {"id": tid, "prompt": prompt[:4000], "status": "queued", "createdAt": now(),
         "startedAt": None, "finishedAt": None, "title": None, "error": None,
         "durationSec": None, "costUsd": None, "turns": None}
    data["tasks"].insert(0, t)
    for old in data["tasks"][KEEP:]:
        d = task_dir(old["id"])
        if os.path.isdir(d):
            for name in os.listdir(d):
                os.remove(os.path.join(d, name))
            os.rmdir(d)
    data["tasks"] = data["tasks"][:KEEP]
    save(data)
    os.makedirs(task_dir(tid), exist_ok=True)
    return t


def start(tid):
    data = load()
    t = find(data, tid) or sys.exit(f"нет задачи {tid}")
    t["status"] = "running"
    t["startedAt"] = now()
    save(data)
    os.makedirs(task_dir(tid), exist_ok=True)
    return t


BROWSER_GLOB = "~/Library/Caches/ms-playwright/chromium-*/chrome-mac*/*.app/Contents/MacOS/*"


def prepare(tid, profile):
    """Каталог задачи: task.txt (промпт + задача) и mcp.json (браузер)."""
    import glob
    d = task_dir(tid)
    os.makedirs(d, exist_ok=True)
    t = find(load(), tid) or sys.exit(f"нет задачи {tid}")
    with open(os.path.join(d, "task.txt"), "w", encoding="utf-8") as f:
        f.write(open(os.path.join(ROOT, "agent", "PROMPT.md"), encoding="utf-8").read())
        f.write("\n## Задача\n\n" + t["prompt"] + "\n")
    # @playwright/mcp по умолчанию ищет системный Google Chrome, которого на mini нет,
    # поэтому указываем сборку Chromium от playwright install (путь версионный — ищем маской).
    args = ["--headless", "--user-data-dir", profile, "--output-dir", d,
            "--viewport-size", "1280x900", "--timeout-navigation", "60000", "--image-responses", "omit"]
    found = sorted(glob.glob(os.path.expanduser(BROWSER_GLOB)))
    if found:
        args += ["--executable-path", found[-1]]
    with open(os.path.join(d, "mcp.json"), "w", encoding="utf-8") as f:
        json.dump({"mcpServers": {"playwright": {"command": "playwright-mcp", "args": args}}}, f)
    return d


def title_from_report(md):
    m = re.search(r"^#\s+(.+)$", md, re.M)
    return m.group(1).strip() if m else None


def finish(tid, report_file=None, error=None, cost=None, turns=None):
    data = load()
    t = find(data, tid) or sys.exit(f"нет задачи {tid}")
    md = ""
    if report_file and os.path.exists(report_file):
        md = open(report_file, encoding="utf-8").read().strip()
    if md:
        dst = os.path.join(task_dir(tid), "report.md")
        os.makedirs(task_dir(tid), exist_ok=True)
        if os.path.abspath(report_file) != os.path.abspath(dst):
            with open(dst, "w", encoding="utf-8") as f:
                f.write(md + "\n")
        t["title"] = title_from_report(md)
    t["status"] = "done" if md and not error else "error"
    t["error"] = error or (None if md else "агент не оставил отчёта")
    t["finishedAt"] = now()
    if t.get("startedAt"):
        try:
            s = datetime.fromisoformat(t["startedAt"].replace("Z", "+00:00"))
            t["durationSec"] = int((datetime.now(timezone.utc) - s).total_seconds())
        except Exception:
            pass
    if cost is not None:
        t["costUsd"] = round(float(cost), 4)
    if turns is not None:
        t["turns"] = int(turns)
    save(data)
    return t


def remove(tid):
    data = load()
    data["tasks"] = [t for t in data["tasks"] if t["id"] != tid]
    save(data)
    d = task_dir(tid)
    if os.path.isdir(d):
        for name in os.listdir(d):
            os.remove(os.path.join(d, name))
        os.rmdir(d)


def reap(data):
    """Задача, которую бросили на полпути (перезапуск serve, убитый процесс), не должна крутиться вечно."""
    changed = False
    for t in data["tasks"]:
        if t["status"] in ("queued", "running"):
            ts = t.get("startedAt") or t.get("createdAt")
            try:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(ts.replace("Z", "+00:00"))).total_seconds()
            except Exception:
                continue
            if age > STALE_SEC:
                t["status"], t["error"], t["finishedAt"] = "error", "прервано (слишком долго)", now()
                changed = True
    return changed


def summary(with_report=None):
    """Что отдаётся странице: список задач + отчёт запрошенной."""
    data = load()
    if reap(data):
        save(data)
    out = {"tasks": []}
    for t in data["tasks"]:
        row = dict(t)
        d = task_dir(t["id"])
        row["hasReport"] = os.path.exists(os.path.join(d, "report.md"))
        row["files"] = sorted(n for n in os.listdir(d)) if os.path.isdir(d) else []
        out["tasks"].append(row)
    if with_report:
        p = os.path.join(task_dir(with_report), "report.md")
        out["report"] = {"id": with_report,
                         "markdown": open(p, encoding="utf-8").read() if os.path.exists(p) else ""}
    out["busy"] = any(t["status"] in ("queued", "running") for t in data["tasks"])
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("add"); p.add_argument("prompt")
    sub.add_parser("list")
    p = sub.add_parser("show"); p.add_argument("id")
    p = sub.add_parser("start"); p.add_argument("id")
    p = sub.add_parser("prepare"); p.add_argument("id"); p.add_argument("--profile", default=os.path.expanduser("~/.agent-browser"))
    p = sub.add_parser("finish"); p.add_argument("id")
    p.add_argument("--report"); p.add_argument("--error"); p.add_argument("--cost"); p.add_argument("--turns")
    p = sub.add_parser("remove"); p.add_argument("id")
    a = ap.parse_args()

    if a.cmd == "add":
        t = add(a.prompt); print(t["id"])
    elif a.cmd == "list":
        for t in load()["tasks"]:
            print(f'{t["id"]}  {t["status"]:<8} {(t.get("title") or t["prompt"])[:70]}')
    elif a.cmd == "show":
        p = os.path.join(task_dir(a.id), "report.md")
        print(open(p, encoding="utf-8").read() if os.path.exists(p) else "(отчёта нет)")
    elif a.cmd == "start":
        start(a.id); print(task_dir(a.id))
    elif a.cmd == "prepare":
        start(a.id); print(prepare(a.id, a.profile))
    elif a.cmd == "finish":
        t = finish(a.id, a.report, a.error, a.cost, a.turns); print(t["status"])
    elif a.cmd == "remove":
        remove(a.id); print("ok")


if __name__ == "__main__":
    main()
