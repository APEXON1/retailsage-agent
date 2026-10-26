# 智能 HR 人才库与问答系统（多账号 RAG Demo）

基于 **Streamlit + LangChain + Ollama + FAISS/BM25 + SQLite** 的本地 RAG 应用：上传简历 → LLM 自动抽取结构化字段 → 混合检索问答 / 标签筛选 / JD 智能匹配，并支持**邮箱注册与按账号的数据隔离**。

## ✨ 功能特性

| 模块 | 说明 |
|---|---|
| 📤 简历上传与自动解析 | PDF/Word/TXT/HTML，PyMuPDF 坐标排序提取 + RapidOCR 兜底，LLM 抽取姓名/年龄/院校/标签等字段，后台队列异步处理 |
| 🔍 RAG 混合检索问答 | FAISS 向量 + BM25 稀疏检索，RRF 融合，带对话记忆与防幻觉 Prompt，回答附带引用来源 |
| 🏷️ 标签结构化检索 | 纯 SQLite LIKE 过滤（AND 语义 / 可选模糊匹配），不依赖大模型 |
| 🤝 JD 智能匹配 | 简历原文 vs 岗位 JD，LLM 从学历/技能/项目三维度评分 |
| 🔐 邮箱注册认证 | PBKDF2 加盐哈希、验证码注册（SMTP 真实发送 / 演示模式自动降级）、密码重置 |
| 👥 多账号数据隔离 | 每个账号的简历文件、DB 记录、RAG 索引 chunk 严格隔离，互不可见 |

## 🚀 快速开始

### 1. 环境要求

- Python ≥ 3.13（推荐用 [uv](https://docs.astral.sh/uv/) 管理依赖）
- [Ollama](https://ollama.com/) 本地服务，并拉取所需模型：

```bash
ollama pull deepseek-r1:8b   # 问答（推理模型）
ollama pull qwen2.5:7b       # 结构化字段抽取
ollama pull nomic-embed-text # 向量嵌入
```

### 2. 安装与启动

```bash
uv sync                 # 安装依赖
uv run streamlit run app.py
```

浏览器访问 `http://localhost:8501`。

### 3. 账号说明

- **旧账号**（迁移后保留）：`hr01` / `hr02` / `hr03`，密码与原静态配置一致，登录框直接输入即可（内部映射为 `hrXX@demo.local`）
- **存量简历**归属 `hr03`，用 `hr03` 登录即可看到
- **新账号**：注册页用邮箱注册（验证码模式见下），注册后进入空白人才库，上传的简历仅自己可见

## 📧 验证码两种模式

| 模式 | 触发条件 | 行为 |
|---|---|---|
| 真实发送 | 配置了 SMTP 环境变量 | 验证码真实发送到注册邮箱 |
| 演示模式 | 未配置 SMTP 或发送失败 | 验证码直接显示在注册页面（标注「演示模式」） |

配置方法：复制 `.env.example` 为 `.env`，填入 QQ/163 邮箱的 SMTP 授权码（`.env` 已在 `.gitignore` 中，不会提交）：

```
SMTP_HOST=smtp.qq.com
SMTP_PORT=465
SMTP_USER=your_name@qq.com
SMTP_PASS=your_16_digit_authcode
```

## 🗂️ 目录结构

```
├── app.py                  # Streamlit 主应用（登录注册 + HR 看板 + 问答 + JD 匹配）
├── auth.py                 # 认证模块：PBKDF2 哈希 / 验证码 / SMTP 双模式发信
├── database.py             # SQLite：users / verification_codes / resumes（owner 隔离）
├── extractor.py            # 文档提取（坐标排序 + OCR 兜底）+ LLM 字段抽取 + JD 匹配
├── rag_engine.py           # RAG 引擎：切分 / FAISS+BM25 混合检索 / RRF 融合 / 带记忆问答
├── config.py               # 集中配置（路径 / 检索参数 / 多账号与 LLM 后端常量）
├── seed_demo.py            # 演示账号 provisioning（一键体验 + 合成简历种子）
├── migrate_multiuser.py    # 一次性迁移脚本（单账号 → 多账号体系）
├── data_demo/              # 合成简历种子数据（虚构人物，随仓库分发）
├── data/<邮箱>/            # 各账号的简历源文件（按 owner 隔离）
├── faiss_index/            # 向量索引（全账号共享，chunk 带 owner 元数据，检索时过滤）
└── resumes.db              # SQLite 数据库
```

## ☁️ 部署到 Streamlit Cloud（拿到公网 demo 链接）

Streamlit Community Cloud 免费提供永久 HTTPS 链接，适合把 demo 发给 HR / 面试官扫码访问。
本项目默认本地 Ollama，云端没有 Ollama，因此内置了 **LLM 后端切换**。

### 1. 准备云端模型 API Key（二选一）

云端用 **OpenAI 兼容接口**，任选一家注册并创建 API Key：

| 服务商 | API_BASE | 说明 |
|---|---|---|
| **OpenRouter**（推荐零成本） | `https://openrouter.ai/api/v1` | 一个 Key 同时覆盖对话 + Embedding，有大量 `:free` 免费模型 |
| 硅基流动 SiliconFlow | `https://api.siliconflow.cn/v1` | 注册送额度，`Qwen/Qwen2.5-7B-Instruct` 有免费额度 |
| DeepSeek | `https://api.deepseek.com/v1` | 按量计费，价格低，中文 JSON 抽取稳 |
| Moonshot | `https://api.moonshot.cn/v1` | 按量计费 |

**OpenRouter 零成本配置（推荐）**：

```bash
LLM_MODEL=deepseek/deepseek-chat              # 对话模型：约 $0.14/百万 token，演示总成本不到 1 分钱
EMBED_MODEL=nvidia/nemotron-3-embed-1b:free   # 向量模型：免费，多语言，32K 上下文
```

> 实测结论（2026-10）：免费 `:free` 模型可用但**慢 30-43 秒且会偶发 503 过载**，
> 演示场景建议用 `deepseek/deepseek-chat`（约 23 秒、稳定、带引用）；
> Embedding 用免费的 `nvidia/nemotron-3-embed-1b:free` 即可（2048 维，实测 12 个 chunk 4.3 秒建完）。
>
> ⚠️ 注意：部分中转站（如 TokenPlus）**不提供 Embedding 模型**，只有对话模型——
> 这会导致 RAG 向量检索无法工作，选型时务必确认平台有 embeddings 接口。

默认配置：切换后端时应用会自动检测索引维度不匹配并重建，无需手工处理。

### 2. 推送到 GitHub

`.gitignore` 已排除 `.env`、`data/`、`resumes.db`、`faiss_index/`、`bm25_index.pkl`，
真实简历与密钥不会进仓库；`data_demo/`（合成简历）会随代码分发。

```bash
git init && git add . && git commit -m "智能 HR 人才库与问答系统"
git remote add origin <你的仓库地址>
git push -u origin main
```

### 3. 在 Streamlit Cloud 创建应用

1. 打开 [share.streamlit.io](https://share.streamlit.io)，用 GitHub 账号登录
2. 「New app」→ 选择仓库 / 分支 `main` / 主文件 `app.py`
3. 「Advanced settings」→ Secrets 中粘贴（**注意键名必须带引号**）：

```toml
LLM_BACKEND = "api"
API_BASE = "https://openrouter.ai/api/v1"
API_KEY = "sk-or-v1-你的密钥"
LLM_MODEL = "stepfun/step-3.5-flash:free"
EMBED_MODEL = "nvidia/nemotron-3-embed-1b:free"

# 可选：真实发送注册验证码（不配则自动降级为演示模式）
SMTP_HOST = "smtp.qq.com"
SMTP_PORT = "465"
SMTP_USER = "your_name@qq.com"
SMTP_PASS = "你的16位授权码"
SMTP_FROM = "your_name@qq.com"
```

4. 点「Deploy」，等 2-5 分钟构建完成，得到 `https://xxx.streamlit.app`

> ⚠️ 若构建报 Python 版本错误，把仓库根目录 `.python-version` 内容改为 `3.12` 再重新部署。

### 4. 云端注意事项

- **数据不持久**：免费实例休眠重启后 SQLite 与上传文件会重置，但 `seed_demo.py`
  会在每次启动时自动重建演示账号和 5 份合成简历，demo 永远可看
- **休眠唤醒**：连续几天无人访问会休眠，首次打开需等 30-60 秒；建议部署后先自己点开一次，
  或配一个免费保活服务（如 UptimeRobot）每 25 分钟访问一次链接
- **手机访问**：HR 大概率用手机扫码进入，部署后请用 4G 网络自测一遍主流程

## 🎯 一键体验（发给 HR 用）

登录页有 **「🎯 一键体验」** 按钮，点击直接进入预置演示账号，无需注册：

- 账号：`demo@demo.local`（也可手动登录，密码 `demo123456`）
- 内置 5 份**虚构合成简历**：Java 后端 5 年 / Vue3 前端 3 年 / AI 应用 4 年 / B 端产品 6 年 / 数据分析应届
- 上传解析、标签检索、RAG 问答、JD 匹配全流程可玩，与真实账号数据完全隔离
- 想恢复初始状态：删除演示账号下已上传的文件后重启即可（种子文件会自动补齐）

## 📱 生成二维码（BOSS 直聘发送）

BOSS 直聘等 IM 对外链有风控，未备案 / 境外域名容易被拦截，**发二维码图片比发链接靠谱**：

```bash
uv run --with qrcode python tools/make_qr.py https://你的应用.streamlit.app
# 生成 demo_qr.png
```

然后在 BOSS 直聘里以**图片**形式发送 `demo_qr.png`，配一句引导：

> 「我做了个 HR 简历管理 + AI 问答的 demo，扫码就能直接体验（不用注册）：能上传简历自动解析、
> 按标签筛人、直接问它『谁有 5 年 Java 经验』。代码也在 GitHub 上，欢迎看。」

> 若二维码图片也被吞，可先发一段 15 秒操作录屏 GIF，再附链接。

## 🔒 数据隔离设计

三层隔离，确保账号 A 永远看不到账号 B 的简历：

1. **文件层**：`data/<邮箱>/` 独立子目录，上传/下载/删除均限定在当前用户目录
2. **数据层**：`resumes` 表主键 `(owner_email, filename)`，所有查询/更新/标签检索强制带 `owner_email` 条件
3. **索引层**：索引物理上只有一份（节省重建成本），但每个 chunk 的 `metadata.owner` 标记归属；检索时先按 owner 过滤候选，再做 BM25 打分与 RRF 融合

## 🛠️ 从旧版本迁移

如果是从「静态账号 + 共享数据」的旧版本升级：

```bash
uv run python migrate_multiuser.py
```

脚本会：备份 `resumes.db` → 归档存量简历到 `hr03@demo.local` → 迁移 3 个旧账号（密码改哈希存储）→ 清理旧索引（下次启动自动按 owner 重建）。

## 📌 常见问题

- **启动后问答提示「知识库为空」**：确认 Ollama 已启动且模型已拉取；首次启动会自动增量构建索引
- **收不到验证码**：未配置 SMTP 时使用演示模式（验证码显示在页面）；配置后请检查垃圾邮件箱
- **忘记密码**：登录页「忘记密码」标签页，通过邮箱验证码重置
