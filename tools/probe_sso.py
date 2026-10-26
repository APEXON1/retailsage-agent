"""探针：用带门户 Cookie 的 WebSocket 连接 Streamlit，验证 SSO 是否生效。

Streamlit 的脚本在 WS 连接建立后、收到第一条 rerun_script 时执行（脚本本身不跑在 HTTP 请求里），
st.context.cookies 取自 WS 握手头 —— 所以这个探针等价于「浏览器打开页面」的那一次运行。

用法（知识库应用需先启动）：
    1) 先从门户拿一个会话 token：
       curl -s -i -X POST http://127.0.0.1:8000/login -d "identifier=demo@demo.local&password=demo123456"
       从响应头的 set-cookie 里取 rs_session 的值
    2) my-rag\\.venv\\Scripts\\python.exe tools\\probe_sso.py <token>   → 应判定「已登录」
       不带 token 再跑一次                                              → 应判定「显示登录页」
"""

import asyncio
import sys

from streamlit.proto.BackMsg_pb2 import BackMsg
from streamlit.proto.ForwardMsg_pb2 import ForwardMsg
import websockets

TOKEN = sys.argv[1] if len(sys.argv) > 1 else ""
URL = "ws://127.0.0.1:8501/_stcore/stream"

# 登录后主界面才有的文本
LOGGED_IN_MARKS = ["人才库管理中心", "返回门户", "安全退出系统", "清空我的全部人才数据"]
# 仅登录页才有的文本
LOGIN_PAGE_MARKS = ["忘记密码", "邮箱注册", "一键体验"]


async def main():
    headers = {"Origin": "http://127.0.0.1:8501"}
    if TOKEN:
        headers["Cookie"] = f"rs_session={TOKEN}"
    try:
        conn = websockets.connect(URL, additional_headers=headers, subprotocols=["streamlit"])
    except TypeError:
        conn = websockets.connect(URL, extra_headers=headers, subprotocols=["streamlit"])

    blobs = []
    async with conn as ws:
        boot = BackMsg()
        boot.rerun_script.query_string = ""
        boot.rerun_script.page_script_hash = ""
        await ws.send(boot.SerializeToString())

        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=12 if not blobs else 4)
            except asyncio.TimeoutError:
                break
            msg = ForwardMsg()
            msg.ParseFromString(raw)
            blobs.append(str(msg))

    blob = "\n".join(blobs)
    print(f"收到 {len(blobs)} 条前向消息，总长 {len(blob)} 字符")
    print("登录态标记:", {m: (m in blob) for m in LOGGED_IN_MARKS})
    print("登录页标记:", {m: (m in blob) for m in LOGIN_PAGE_MARKS})
    print("页面里出现 hr03@demo.local:", "hr03@demo.local" in blob)
    verdict = "已登录（SSO 生效）" if any(m in blob for m in LOGGED_IN_MARKS) else "未登录（显示登录页）"
    print("判定:", verdict)


asyncio.run(main())
