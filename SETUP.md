# 本机运行说明

这套环境已经在本机配好，只剩 **LLM API Key** 需要你自己填。

---

## 一、启动

双击根目录的 **`start.bat`**，浏览器会自动打开 <http://127.0.0.1:7860>。
关掉黑窗口即停止服务。

命令行方式：

```bash
.venv/Scripts/python.exe app.py      # Web 界面
.venv/Scripts/python.exe -m data_insight.main   # 命令行版
```

## 二、配置 LLM（唯一必做的一步）

打开根目录 `.env`，填这一行：

```env
LLM_API_KEY=你的key
```

默认接 DeepSeek（`LLM_BASE_URL=https://api.deepseek.com`，`LLM_MODEL_ID=deepseek-chat`）。
换其他 OpenAI 兼容服务时，把 `LLM_BASE_URL` 和 `LLM_MODEL_ID` 一起改掉即可。
**改完必须重启服务。**

没填 key 时界面不会崩，只会在对话里提示你去填。

## 三、已经准备好的东西

| 项目 | 位置 | 说明 |
|------|------|------|
| 虚拟环境 | `.venv/` | Python 3.13.5，依赖全部装好（torch 2.14 CPU / chromadb 1.5.9 / gradio 6.30 / sentence-transformers 6.2） |
| 测试数据库 | `data_insight/test_retail.db` | 40 门店 / 500 客户 / 23555 订单，日期覆盖 2026-04-28 ~ 2026-10-24 |
| 向量模型 | `data_insight/models/bge-small-zh-v1.5/` | 96MB，从 ModelScope 下载，**离线可用**，不依赖 HuggingFace |
| 知识索引 | `data_insight/memory/vector_store/` | 33 篇文档切成 44 个块，已建好 |

## 四、换自己的数据

**最简单的路子**：把 `.env` 里的 `SQLITE_DATABASE` 指向你自己的 `.db` 文件，表名和字段名对齐 `orders` / `customers` / `stores` 三张表即可（结构见 README）。

如果字段名不同，要同步改两处硬编码的表结构：
- `app.py` 的 `init_agent()` 里 `table_schemas`
- `data_insight/agents/insight_agent.py` 的 `INSIGHT_SYSTEM_PROMPT`

## 五、已知限制

- **只支持 SQLite**。`config.py` 里虽然写了 MySQL / PostgreSQL / ClickHouse 的配置，但系统提示词把 SQLite 语法写死了，换库要改提示词。
- **单 Agent**。`understand_agent.py` / `analysis_agent.py` / `expression_agent.py` 是死代码，没有任何地方调用，README 里的"子代理机制"名不副实。
- **首次对话会慢**。要加载 embedding 模型 + 建索引，约 10-20 秒，之后走缓存。
- 单轮最多 20 步推理（`MAX_STEPS`，在 `.env` 里可调）。

## 六、排错

日志在 `data_insight/logs/retailsage.log`。

| 现象 | 原因 |
|------|------|
| 对话提示"还没配置 LLM API Key" | `.env` 里 `LLM_API_KEY` 为空 |
| 启动报 `ModuleNotFoundError` | 没用 `.venv` 里的 Python，或依赖装坏了，重跑 `pip install -r requirements.txt` |
| 界面白屏 / 样式错乱 | 浏览器缓存，硬刷新 Ctrl+F5 |
| 装包很慢 | 已在 `.venv/pip.ini` 配好清华源 |
