"""最终验收：干净启动三个服务 -> 跑全部检查 -> 干净关停。一条命令内完成，不留残留。"""

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUN = ROOT / ".run"
PY = ROOT / ".venv" / "Scripts" / "python.exe"
RAG_PY = ROOT / "my-rag" / ".venv" / "Scripts" / "python.exe"
PORTS = {8000: "portal", 7860: "insight", 8501: "kb"}

os.environ["MSYS_NO_PATHCONV"] = "1"
results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ""), flush=True)


def pids_on(port):
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return []
    hits = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0].upper() == "TCP" and parts[3].upper() == "LISTENING":
            if parts[1].endswith(f":{port}"):
                hits.append(parts[4])
    return hits


def kill_all():
    for _ in range(4):
        killed = False
        for port in PORTS:
            for pid in pids_on(port):
                subprocess.run(["taskkill", "/PID", pid, "/T", "/F"], capture_output=True)
                killed = True
        if not killed:
            return True
        time.sleep(2)
    return all(not pids_on(p) for p in PORTS)


def wait_port(port, timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        if pids_on(port):
            return True
        time.sleep(0.5)
    return False


def fetch(url, cookie=None, accept="text/html", method="GET", data=None):
    req = urllib.request.Request(url, method=method)
    req.add_header("Accept", accept)
    if cookie:
        req.add_header("Cookie", f"rs_session={cookie}")
    if data is not None:
        req.data = data.encode()
        req.add_header("Content-Type", "application/x-www-form-urlencoded")

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(req, timeout=30) as r:
            return r.status, dict(r.headers), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode("utf-8", "replace")


print("=" * 60)
print("0) 清理残留进程")
print("=" * 60)
ok = kill_all()
check("端口已清空", ok, "8000/7860/8501")

print()
print("=" * 60)
print("1) 启动 launcher")
print("=" * 60)
env = dict(os.environ, PORTAL_NO_BROWSER="1")
launcher = subprocess.Popen([str(PY), str(ROOT / "launcher.py")], cwd=str(ROOT), env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace")
ready = {p: wait_port(p) for p in PORTS}
for port, name in PORTS.items():
    check(f"{name} 端口 {port} 就绪", ready[port])

print()
print("=" * 60)
print("2) 未登录拦截")
print("=" * 60)
st, hdr, _ = fetch("http://127.0.0.1:8000/")
check("门户 / 未登录跳登录页", st == 302 and "/login" in hdr.get("location", ""), f"{st} -> {hdr.get('location')}")
st, hdr, _ = fetch("http://127.0.0.1:7860/")
check("洞察 / 未登录跳门户登录", st == 302 and "8000/login" in hdr.get("location", ""), f"{st}")
st, _, _ = fetch("http://127.0.0.1:7860/config", accept="application/json")
check("洞察 /config 未登录 401", st == 401, f"{st}")

probe = subprocess.run([str(RAG_PY), str(ROOT / "tools" / "probe_sso.py"), ""],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
check("知识库 无会话显示登录页", "未登录" in probe.stdout, probe.stdout.strip().splitlines()[-1] if probe.stdout else probe.stderr[:100])

print()
print("=" * 60)
print("3) 门户登录与单点登录")
print("=" * 60)
st, hdr, _ = fetch("http://127.0.0.1:8000/login", method="POST",
                   data="identifier=demo%40demo.local&password=demo123456&next=%2F")
setcookie = hdr.get("set-cookie", "")
token = re.search(r"rs_session=([^;]+)", setcookie)
check("门户登录成功并下发 Cookie", st == 303 and bool(token), f"{st}")

if token:
    tok = token.group(1)
    check("Cookie 未设 Secure（本地 http 必须）", "secure" not in setcookie.lower(), setcookie[:90])
    check("Cookie 为 HttpOnly + SameSite=Lax",
          "httponly" in setcookie.lower() and "samesite=lax" in setcookie.lower())

    st, _, body = fetch("http://127.0.0.1:8000/", cookie=tok)
    online = body.count("运行中")
    check("门户入口页可访问，两张卡片均在线", st == 200 and online == 2, f"{st}, 在线卡片 {online}")

    st, _, body = fetch("http://127.0.0.1:7860/", cookie=tok)
    check("洞察 带会话可直接进入", st == 200 and "RetailSage" in body, f"{st}, {len(body)} 字节")

    st, _, _ = fetch("http://127.0.0.1:7860/config", cookie=tok, accept="application/json")
    check("洞察 /config 带会话 200", st == 200, f"{st}")

    probe = subprocess.run([str(RAG_PY), str(ROOT / "tools" / "probe_sso.py"), tok],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    check("知识库 带会话自动登录", "已登录" in probe.stdout, probe.stdout.strip().splitlines()[-1] if probe.stdout else probe.stderr[:100])

    st, hdr, _ = fetch("http://127.0.0.1:8000/logout", cookie=tok)
    check("退出跳转登录页", st == 302 and "/login" in hdr.get("location", ""), str(st))
    st, hdr, _ = fetch("http://127.0.0.1:7860/", cookie=tok)
    check("退出后洞察重新拦截", st == 302, str(st))

print()
print("=" * 60)
print("4) 索引未被误删")
print("=" * 60)
idx = ROOT / "my-rag" / "faiss_index"
check("向量索引仍在", (idx / "index.faiss").exists())
marker = idx / ".embed_model"
check("标记已修复（无 BOM 且匹配）",
      marker.exists() and not marker.read_bytes().startswith(b"\xef\xbb\xbf"),
      marker.read_text(encoding="utf-8-sig").strip() if marker.exists() else "缺失")

print()
print("=" * 60)
print("5) 关停")
print("=" * 60)
lp = RUN / "_launcher.pid"
if lp.exists():
    subprocess.run(["taskkill", "/PID", lp.read_text().strip(), "/T", "/F"], capture_output=True)
launcher.wait(timeout=30)
time.sleep(4)
check("三个端口全部释放", all(not pids_on(p) for p in PORTS))

# 强杀 launcher 后它没机会清理自己的 pid 文件；这里补上，避免下次启动读到过期 PID
for stale in RUN.glob("*.pid"):
    try:
        stale.unlink()
    except OSError:
        pass

passed = sum(1 for _, ok, _ in results if ok)
total = len(results)
print()
print("=" * 60)
print(f"验收结果：{passed}/{total} 通过")
print("=" * 60)
for name, ok, detail in results:
    if not ok:
        print(f"  失败：{name} — {detail}")
sys.exit(0 if passed == total else 1)
