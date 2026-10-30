"""一键启动：门户（8000）+ RetailSage 洞察（7860）+ 智能 HR 知识库（8501）。

三个进程各自独立，任何一个崩了都不影响另外两个；关掉本窗口会连着把它们收掉。

为什么不是 `start` 弹三个窗口：那样输出分散、关窗口会留下孤儿进程、也没有 PID 可精确回收。
这里用「单控制台 + 输出前缀 + Job Object」的方式：
- 输出统一加 [portal]/[insight]/[kb] 前缀；
- Job Object 设 KILL_ON_JOB_CLOSE，连「用户点窗口 X 强杀 launcher」都能兜住；
- 每个子进程的 PID 写到 .run/*.pid，stop.py 据此精确 kill。

用法：
    python launcher.py                     # 三个全起
    python launcher.py --only portal        # 只起门户
    python launcher.py --only insight,kb    # 起两个

注意：所有 .bat 都只是这个脚本的一行外壳。判断逻辑全在 Python 里，
因为 cmd.exe 按本地代码页解析批处理，中文和括号块很容易被误读。
"""

from __future__ import annotations

import ctypes
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from ctypes import wintypes
from pathlib import Path

# 控制台输出统一走 UTF-8，避免中文在重定向/管道下按本地代码页编码而报错
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent
RUN_DIR = ROOT / ".run"
RAG_PY = ROOT / "my-rag" / ".venv" / "Scripts" / "python.exe"
OWN_PY = ROOT / ".venv" / "Scripts" / "python.exe"

PORTAL_PORT = int(os.getenv("PORTAL_PORT", "8000"))
INSIGHT_PORT = int(os.getenv("INSIGHT_PORT", "7860"))
KB_PORT = int(os.getenv("KB_PORT", "8501"))

# 这些前缀的环境变量可能是用户 shell 里残留的，不进子进程，避免覆盖各自 .env 的配置
_DROP_PREFIXES = (
    "LLM_", "API_", "SMTP_", "EMBED", "SQLITE_", "VECTOR_", "LOG_",
    "DEFAULT_DB", "COLLECTION_", "MAX_", "CACHE_", "HF_",
)

CREATE_NEW_PROCESS_GROUP = 0x00000200
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JobObjectExtendedLimitInformation = 9


def _task_env() -> dict:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(_DROP_PREFIXES)}
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _port_busy(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _wait_port_free(port: int, timeout: float = 15.0) -> bool:
    """等端口释放。上一次运行留下的进程可能还没完全退出，别急着判失败。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _port_busy(port):
            return True
        time.sleep(0.5)
    return not _port_busy(port)


def _wait_port(port: int, timeout: float = 75.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _port_busy(port):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    return True
            except OSError:
                pass
        time.sleep(0.4)
    return False


def _pid_on_port(port: int) -> str | None:
    """查真正在监听该端口的进程 PID。

    某些环境下 Popen 拿到的是外层包装进程，真正干活的服务器是它的子进程；
    把这些 PID 一起写进 .run/*.pid，stop.bat 才能精确回收。
    """
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=15).stdout
    except Exception:
        return None
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0].upper() == "TCP" and parts[3].upper() == "LISTENING":
            if parts[1].endswith(f":{port}"):
                return parts[4]
    return None


def _write_pid(name: str, pids) -> None:
    uniq = []
    for p in pids:
        if p and str(p) not in uniq:
            uniq.append(str(p))
    (RUN_DIR / f"{name}.pid").write_text("\n".join(uniq), encoding="utf-8")


# ==================== Job Object：兜住「关窗口」场景 ====================
class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _BASIC_LIMIT(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _EXTENDED_LIMIT(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BASIC_LIMIT),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _make_job():
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = _EXTENDED_LIMIT()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = k32.SetInformationJobObject(
            job, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info))
        return (k32, job) if ok else None
    except Exception:
        return None


# ==================== 子进程定义 ====================
def _tasks() -> list[dict]:
    return [
        {
            "name": "portal",
            "exe": sys.executable,
            "args": ["-m", "uvicorn", "portal.main:app",
                     "--host", "127.0.0.1", "--port", str(PORTAL_PORT)],
            "cwd": ROOT,
            "port": PORTAL_PORT,
        },
        {
            "name": "insight",
            "exe": sys.executable,
            "args": [str(ROOT / "run_insight_web.py")],
            "cwd": ROOT,
            "port": INSIGHT_PORT,
            "env": {"INSIGHT_PORT": str(INSIGHT_PORT), "PORTAL_URL": f"http://127.0.0.1:{PORTAL_PORT}"},
        },
        {
            "name": "kb",
            "exe": str(RAG_PY),
            # 用 venv 的 python.exe 直接跑脚本（不能用 Scripts\*.exe 控制台壳：
            # 那种壳里内嵌了创建时的绝对路径，换机器就失效）。
            # run_kb_web.py 内部会 os.chdir 到 my-rag 并扩 sys.path，与启动 CWD 无关。
            "args": [str(ROOT / "my-rag" / "run_kb_web.py")],
            "cwd": ROOT / "my-rag",
            "port": KB_PORT,
            "env": {"KB_PORT": str(KB_PORT), "PORTAL_URL": f"http://127.0.0.1:{PORTAL_PORT}"},
        },
    ]


def _pump(prefix: str, stream) -> None:
    for line in iter(stream.readline, ""):
        print(f"{prefix} {line.rstrip()}", flush=True)
    stream.close()


def _parse_only(argv: list[str]) -> set[str]:
    """解析 --only portal[,insight,kb]。空集合表示全部。"""
    names: set[str] = set()
    for i, arg in enumerate(argv):
        if arg == "--only" and i + 1 < len(argv):
            names.update(x.strip().lower() for x in argv[i + 1].split(",") if x.strip())
        elif arg.startswith("--only="):
            names.update(x.strip().lower() for x in arg.split("=", 1)[1].split(",") if x.strip())
    return names


def _validate() -> str | None:
    """启动前的前置检查，返回错误信息（None 表示通过）。

    门户 / 洞察两个任务都用 sys.executable 运行，真正决定成败的是「当前解释器有没有
    装齐依赖」。项目 .venv 只是推荐位置：本机若用 Anaconda 等已装好依赖的解释器直接
    跑同样可以，因此 .venv 缺失不再一票否决——有则建议对齐，无则靠下面的依赖校验兜底。
    """
    if OWN_PY.exists():
        if Path(sys.executable).resolve() != OWN_PY.resolve():
            print(f"[提示] 当前解释器不是项目虚拟环境：{sys.executable}")
            print(f"       建议改用：{OWN_PY}")
    else:
        print(f"[提示] 未发现项目 .venv（{OWN_PY}），改用当前解释器：{sys.executable}")
    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401
    except ImportError as e:
        if OWN_PY.exists():
            hint = f"请用项目 .venv 里的 python 运行：{OWN_PY}"
        else:
            hint = "请用已装齐依赖的解释器运行（如 Anaconda），或先 pip install -r requirements.txt"
        return f"当前解释器缺少依赖（{e.name}）：{sys.executable}\n       {hint}。"
    return None


def main() -> int:
    if "-h" in sys.argv or "--help" in sys.argv:
        print(__doc__)
        return 0

    err = _validate()
    if err:
        print(f"[错误] {err}")
        return 1

    RUN_DIR.mkdir(exist_ok=True)
    only = _parse_only(sys.argv[1:])
    tasks = _tasks()
    if only:
        unknown = only - {t["name"] for t in tasks}
        if unknown:
            print(f"[错误] 未知的服务名：{', '.join(sorted(unknown))}（可选：portal / insight / kb）")
            return 1
        tasks = [t for t in tasks if t["name"] in only]

    if any(t["name"] == "kb" for t in tasks) and not RAG_PY.exists():
        print(f"[错误] 找不到知识库应用的虚拟环境：{RAG_PY}")
        print("       请先在 my-rag 目录下重建：uv venv .venv --python <python> && uv sync --frozen")
        return 1

    busy = [(t["name"], t["port"]) for t in tasks if not _wait_port_free(t["port"])]
    if busy:
        print("[错误] 以下端口已被占用，可能上一次的服务还在跑：")
        for name, port in busy:
            print(f"   - {name}: 127.0.0.1:{port}")
        print("请先运行 stop.bat 停止旧服务，或手动结束占用进程后重试。")
        return 1

    job = _make_job()
    env_base = _task_env()
    procs: list[tuple[str, subprocess.Popen]] = []

    # 端口已确认空闲 → 旧的 pid 文件必然是上一轮残留（可能是被强杀后没来得及清）。
    # 必须删掉，否则 Windows 复用 PID 后 stop.py 可能误杀无关进程。
    for stale in RUN_DIR.glob("*.pid"):
        try:
            stale.unlink()
        except OSError:
            pass

    # 记下 launcher 自己的 PID：stop.py 先停它，再由它的 finally 负责收拾子进程
    (RUN_DIR / "_launcher.pid").write_text(str(os.getpid()), encoding="utf-8")

    try:
        for t in tasks:
            env = dict(env_base)
            env.update(t.get("env") or {})
            proc = subprocess.Popen(
                [t["exe"], *t["args"]],
                cwd=str(t["cwd"]),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=CREATE_NEW_PROCESS_GROUP,
            )
            procs.append((t["name"], proc))
            (RUN_DIR / f"{t['name']}.pid").write_text(str(proc.pid), encoding="utf-8")
            threading.Thread(
                target=_pump, args=(f"[{t['name']}]", proc.stdout), daemon=True
            ).start()
            print(f"[启动] {t['name']:<8} pid={proc.pid:<7} 端口 {t['port']}")
            if job:
                try:
                    job[0].AssignProcessToJobObject(job[1], wintypes.HANDLE(int(proc._handle)))
                except Exception:
                    pass

        print("\n等待服务就绪…")
        for t in tasks:
            ok = _wait_port(t["port"])
            if ok:
                real = _pid_on_port(t["port"])
                wrapper = next((p.pid for n, p in procs if n == t["name"]), None)
                _write_pid(t["name"], [wrapper, real])
            print(f"  {t['name']:<8} {'就绪' if ok else '超时未响应'}  http://127.0.0.1:{t['port']}")

        print("\n" + "=" * 62)
        labels = {"portal": "门户入口  ", "insight": "数据洞察  ", "kb": "智能知识库"}
        for t in tasks:
            print(f"  {labels.get(t['name'], t['name'])} http://127.0.0.1:{t['port']}/")
        print("=" * 62)
        print(f"关闭本窗口或按 Ctrl+C 会停止 {'/'.join(t['name'] for t in tasks)}。\n")

        opens_portal = any(t["name"] == "portal" for t in tasks)
        if opens_portal and os.getenv("PORTAL_NO_BROWSER") != "1":
            webbrowser.open(f"http://127.0.0.1:{PORTAL_PORT}/")
        elif not opens_portal:
            print(f"提示：门户未启动，浏览器需先登录 {PORTAL_PORT} 端口才能进入本服务。\n")

        # 盯着子进程：任何一个意外退出都要报一次，但不要连坐其它的
        reported: set[str] = set()
        while True:
            for name, proc in procs:
                code = proc.poll()
                if code is not None and name not in reported:
                    reported.add(name)
                    print(f"[退出] {name} 已结束，返回码 {code}（其它服务不受影响）", flush=True)
            if len(reported) == len(procs):
                print("[提示] 所有服务都已结束。")
                return 0
            time.sleep(2)

    except KeyboardInterrupt:
        print("\n[停止] 收到中断信号，正在结束子进程…")
        return 0
    finally:
        _shutdown(procs)


def _shutdown(procs) -> None:
    for name, proc in procs:
        if proc.poll() is None:
            try:
                proc.send_signal(0x0001)  # CTRL_BREAK_EVENT
            except Exception:
                pass
    for name, proc in procs:
        try:
            proc.wait(timeout=6)
        except Exception:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True)
    for name, _ in procs:
        try:
            (RUN_DIR / f"{name}.pid").unlink(missing_ok=True)
        except Exception:
            pass
    try:
        (RUN_DIR / "_launcher.pid").unlink(missing_ok=True)
    except Exception:
        pass
    print("[停止] 全部服务已结束。")


if __name__ == "__main__":
    sys.exit(main())
