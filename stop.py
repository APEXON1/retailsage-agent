"""停止由 launcher.py 启动的服务（门户 8000 / 洞察 7860 / 知识库 8501）。

用法：
    .venv\\Scripts\\python.exe stop.py

逻辑：
1) 先按 .run/*.pid 精确结束（_launcher 优先，/T 会连带它的子进程）；
2) 再对仍占用这三个端口的进程做兜底清理（pid 文件丢失时的唯一出路）；
3) 最后回报端口状态。

批处理 stop.bat 只是这个脚本的一行外壳 —— 判断逻辑放在 Python 里，
因为 cmd.exe 按本地代码页解析批处理，中文和括号块极易被误读。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent
RUN_DIR = ROOT / ".run"
PORTS = {8000: "portal", 7860: "insight", 8501: "kb"}
PID_ORDER = ["_launcher", "portal", "insight", "kb"]


def pids_on(port: int) -> list[str]:
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return []
    hits: list[str] = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0].upper() == "TCP" and parts[3].upper() == "LISTENING":
            if parts[1].endswith(f":{port}") and parts[4] not in hits:
                hits.append(parts[4])
    return hits


def kill(pid: str) -> bool:
    r = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    return r.returncode == 0


def main() -> int:
    print("=" * 54)
    print("停止服务")
    print("=" * 54)

    killed = 0
    seen: set[str] = set()

    if RUN_DIR.exists():
        for name in PID_ORDER:
            f = RUN_DIR / f"{name}.pid"
            if not f.exists():
                continue
            for pid in [x.strip() for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]:
                if pid in seen:
                    continue
                seen.add(pid)
                if kill(pid):
                    print(f"  已结束进程 {pid:<7}（来自 {name}.pid）")
                    killed += 1
            try:
                f.unlink()
            except OSError:
                pass

    # 兜底：pid 文件可能已被清掉或进程被重新拉起，按端口再扫一次
    time.sleep(2)
    for port, name in PORTS.items():
        for pid in pids_on(port):
            if pid in seen:
                continue
            seen.add(pid)
            if kill(pid):
                print(f"  已结束进程 {pid:<7}（占用 {port}，{name}）")
                killed += 1

    time.sleep(2)
    left = {port: pids_on(port) for port in PORTS}
    left = {p: v for p, v in left.items() if v}
    print()
    if killed == 0 and not left:
        print("没有正在运行的服务，无需停止。")
    elif left:
        print("以下端口仍被占用，可能不是本项目的进程：")
        for port, pids in left.items():
            print(f"  {port}: PID {', '.join(pids)}")
        print("请到任务管理器手动结束。")
        return 1
    else:
        print(f"完成：共结束 {killed} 个进程，三个端口均已释放。")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
