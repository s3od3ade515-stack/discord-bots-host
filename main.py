"""
مشغّل البوتات — يشغّل كل بوت داخل مجلد bots/ تلقائيًا 24/7:
  - كل مجلد فيه bot.py (أو main.py) يعتبر بوت.
  - إذا البوت وقف أو كراش يعيد تشغيله تلقائيًا.
  - صفحة ويب بسيطة لحالة البوتات (/) و (/health) عشان الاستضافة تظل شغالة.
"""
import asyncio
import html
import os
import sys
import time
from collections import deque
from pathlib import Path

from aiohttp import web

BASE = Path(__file__).parent.resolve()
BOTS_DIR = BASE / "bots"
PORT = int(os.getenv("PORT", "8080"))
STATUS_KEY = os.getenv("STATUS_KEY", "")      # إذا حطيته: تظهر السجلات فقط لمن يفتح /?key=...
ENTRY_NAMES = ("bot.py", "main.py")
MAX_BACKOFF = 60

state: dict[str, dict] = {}


def load_env_file(path: Path) -> dict:
    env = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def discover():
    found = []
    if not BOTS_DIR.exists():
        return found
    for folder in sorted(BOTS_DIR.iterdir()):
        if not folder.is_dir() or folder.name.startswith(("_", ".")):
            continue
        for name in ENTRY_NAMES:
            if (folder / name).exists():
                found.append((folder.name, folder, name))
                break
    return found


async def run_bot(name: str, folder: Path, entry: str):
    st = state[name]
    backoff = 5
    while True:
        env = {**os.environ, **load_env_file(folder / ".env"), "PYTHONUNBUFFERED": "1"}
        st.update(status="running", started=time.time())
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-u", entry, cwd=str(folder), env=env,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
            st["pid"] = proc.pid
            async for raw in proc.stdout:
                line = raw.decode(errors="replace").rstrip()
                print(f"[{name}] {line}", flush=True)
                st["logs"].append(line)
            code = await proc.wait()
        except Exception as ex:
            code = -1
            st["logs"].append(f"runner error: {ex}")
        uptime = time.time() - st["started"]
        st.update(status="restarting", last_exit=code, pid=None)
        st["restarts"] += 1
        backoff = 5 if uptime > 120 else min(backoff * 2, MAX_BACKOFF)
        print(f"[runner] {name} وقف (code {code}) — إعادة تشغيل بعد {backoff}ث", flush=True)
        await asyncio.sleep(backoff)


def fmt_uptime(sec):
    sec = int(sec)
    d, r = divmod(sec, 86400)
    h, r = divmod(r, 3600)
    m, s = divmod(r, 60)
    return (f"{d}ي " if d else "") + f"{h}س {m}د {s}ث"


async def index(request: web.Request):
    show_logs = bool(STATUS_KEY) and request.query.get("key") == STATUS_KEY
    rows = []
    for name, st in state.items():
        running = st["status"] == "running"
        up = fmt_uptime(time.time() - st["started"]) if running and st["started"] else "—"
        logs = ""
        if show_logs:
            logs = f"<pre>{html.escape(chr(10).join(list(st['logs'])[-15:]))}</pre>"
        rows.append(
            f"<tr><td>{html.escape(name)}</td>"
            f"<td class={'ok' if running else 'bad'}>{'🟢 شغّال' if running else '🔴 يعيد التشغيل'}</td>"
            f"<td>{up}</td><td>{st['restarts']}</td><td>{st['last_exit'] if st['last_exit'] is not None else '—'}</td></tr>"
            + (f"<tr><td colspan=5>{logs}</td></tr>" if logs else "")
        )
    body = f"""<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>حالة البوتات</title>
<style>body{{font-family:system-ui,sans-serif;background:#12101a;color:#eee;max-width:760px;margin:30px auto;padding:0 14px}}
h1{{color:#b388ff}}table{{width:100%;border-collapse:collapse}}td,th{{padding:10px;border-bottom:1px solid #2c2740;text-align:right}}
.ok{{color:#5fe08a}}.bad{{color:#ff6b6b}}pre{{background:#0b0a12;padding:10px;border-radius:8px;overflow:auto;direction:ltr;text-align:left;font-size:12px}}</style>
<h1>🤖 حالة البوتات</h1>
<table><tr><th>البوت</th><th>الحالة</th><th>مدة التشغيل</th><th>إعادات التشغيل</th><th>آخر خروج</th></tr>
{''.join(rows) or '<tr><td colspan=5>ما فيه بوتات داخل مجلد bots/</td></tr>'}</table></html>"""
    return web.Response(text=body, content_type="text/html")


async def health(_):
    return web.Response(text="ok")


async def main():
    if os.getenv("AUTO_UPDATE_YTDLP", "1") == "1":
        p = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "pip", "install", "-U", "-q", "yt-dlp",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await p.wait()

    bots = discover()
    print(f"[runner] لقيت {len(bots)} بوت: {', '.join(b[0] for b in bots) or '—'}", flush=True)
    for name, _, _ in bots:
        state[name] = dict(status="starting", started=None, restarts=0, last_exit=None, pid=None, logs=deque(maxlen=200))

    app = web.Application()
    app.add_routes([web.get("/", index), web.get("/health", health)])
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    print(f"[runner] صفحة الحالة على المنفذ {PORT}", flush=True)

    await asyncio.gather(*(run_bot(n, f, e) for n, f, e in bots), asyncio.Event().wait())


if __name__ == "__main__":
    asyncio.run(main())
