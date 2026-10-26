"""总集门户：统一登录 + 应用入口。

三个独立进程共用一套账号（accounts/accounts.db）与一个会话 Cookie，
所以本包只依赖标准库 + fastapi/uvicorn/jinja2，**绝不 import 任何业务应用模块**
（否则会让 8000 端口也去加载 gradio、创建 data_insight 目录、读错 .env）。
"""
