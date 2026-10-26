# RetailSage Agent

> 🤖 基于 HelloAgents 框架的零售/电商数据分析智能体，让每个人都能用对话的方式从数据中获得可行动的洞察。

[![Python](https://img.shields.io/badge/Python-3.9+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

---

## 🧭 本仓库的组成（先看这里）

这个目录里同时有**两个独立应用**，外加一个**统一门户**：

| 服务 | 端口 | 技术栈 | 位置 |
|---|---|---|---|
| 总集门户（登录 + 入口卡片） | 8000 | FastAPI + Jinja2 | `portal/` |
| RetailSage 数据洞察 | 7860 | Gradio + HelloAgents | 根目录 `app.py` / `data_insight/` |
| 智能 HR 知识库 | 8501 | Streamlit + FAISS | `my-rag/` |

三者是**三个独立进程**，共用一套账号（`accounts/accounts.db`）和一个会话 Cookie。
Cookie 的作用域是主机名、不含端口，所以门户登录一次，浏览器访问另外两个端口时会自动带上，
无需二次输入密码。任一服务崩溃或重启都不影响另外两个。

### 启动方式

| 场景 | 操作 |
|---|---|
| 全部启动（推荐） | 双击 `start.bat`，自动打开 http://127.0.0.1:8000/ |
| 只启某一个 | `start-portal.bat` / `start-insight.bat` / `start-rag.bat` |
| 停止全部 | `stop.bat` |

`.bat` 只是外壳，真正的逻辑在 `launcher.py` 与 `stop.py`（批处理里刻意不写任何中文与复杂语法）：

```
.venv\Scripts\python.exe launcher.py                 三个都起
.venv\Scripts\python.exe launcher.py --only portal    只起门户（insight / kb 同理）
.venv\Scripts\python.exe stop.py                       停全部
```

> ⚠️ 维护提醒：改 `.bat` 必须保存为**纯 ASCII + CRLF 换行**。cmd.exe 按本地代码页（GBK）逐行解析，
> UTF-8 中文会变乱码，多字节序列还会破坏行结构，导致 `echo` 的内容被当成命令执行
> （表现为一串 `'xxx' is not recognized as an internal or external command`）。

账号与原有账号完全一致：演示账号 `demo@demo.local / demo123456`，旧账号 `hr03` 等输入简写也可登录。
注册与找回密码仍在「智能 HR 知识库」的登录页内完成（同一套账号，改完门户立即可用）。

### 数据隔离

| 范围 | 隔离方式 |
|---|---|
| 洞察应用的用户数据 | 会话历史 / 用户画像 / 图表 / 报告按账号分目录：`data_insight/memory/users/<账号>/`、`data_insight/output/users/<账号>/` |
| 洞察应用的上传数据 | 界面上传的数据库副本 `data_insight/memory/users/<账号>/data.db`、上传的知识文档 `.../uploads/kb/`、独立向量库 `.../vector_store/`，均按账号隔离 |
| 知识库应用的用户数据 | 简历文件 `my-rag/data/<邮箱>/`、DB 主键 `(owner_email, filename)`、索引 chunk 的 `metadata.owner` |
| 全站共享（有意为之） | 业务库 `test_retail.db`（作为上传数据库的初始模板）、零售行业知识库、账号库 |

### 运维脚本

| 脚本 | 用途 |
|---|---|
| `tools/acceptance_check.py` | 端到端验收：干净启停 + 认证拦截 + 单点登录 + 索引完整性 |
| `tools/probe_api.py` | 检查 `.env` 里的云端 API 能否跑 embedding 与对话（切换后端前必跑） |
| `tools/rebuild_index.py` | 切换 embedding 模型后重建知识库索引（旧索引只改名不删除） |
| `tools/probe_sso.py` | 验证知识库应用的 SSO 是否生效 |

> ⚠️ 全链路必须使用 `127.0.0.1`，**不要用 `localhost`**。二者是不同的 host，Cookie 不互通，
> 且会让子应用的 fetch / WebSocket 变成跨站请求，表现为「页面能打开、一点就 401」。

---

## ✨ 项目亮点

### 1. 数据故事，而非数据图表 📖

传统 BI 给你一张折线图，让你自己猜原因。RetailSage 给你一个**完整的故事**：

```
📊 概况：华东区6月GMV为1.2亿，环比下降12%。

🔍 主要原因（按影响度排序）：
1. A门店（影响占比45%）：竞对"XX品牌"6月在周边新开业，客流分流
2. B门店（影响占比30%）：5月大促透支了6月需求，复购率下降18%
3. C门店（影响占比15%）：供应链问题导致SKU缺货率升至23%

💡 建议：
· A门店：加强会员粘性运营，推出专属优惠对抗竞对
· B门店：控制促销节奏，增加6月独立活动
· C门店：优先解决供应链，预计恢复周期2周
```

### 2. 知识增强，行业深度 🏪

通过 RAG 系统集成零售/电商行业知识，Agent 能理解"GMV"="成交总额"，"复购率"="回头率"等业务术语。

**RAGAS 评测结果：**
- 检索成功率：**100%**
- 上下文相关性：**94%**

### 3. 业务记忆，越用越懂你 🧠

| 阶段 | 体验 |
|------|------|
| 第 1 次对话 | 你问"上个月业绩怎么样"，它给你全局概览 |
| 第 5 次对话 | 它知道你关心华东区、关注 GMV 和毛利率 |
| 第 10 次对话 | 它主动告诉你"A门店本周异常，建议关注" |

### 4. 白盒分析，过程透明 🔍

用户可以展开查看 Agent 的完整推理链路，包括 SQL 查询、分析逻辑、置信度标注。

### 5. 一键出报告，从数据到交付物 📄

支持 PDF、PowerPoint、Markdown 三种格式输出。

### 6. 零门槛接入自有数据 📎

点击界面右上角 **➕** 号即可上传文件，无需改任何代码：

| 上传类型 | 支持格式 | 效果 |
|---|---|---|
| 📊 导入数据库 | CSV / Excel(.xlsx) | 以文件名自动建表，立刻可用自然语言查询（如「统计 `sales_2024` 的行数」） |
| 📚 加入知识库 | PDF / Word / TXT / Markdown | 自动解析、分块、向量化并重建索引，Agent 随即可引用其中内容 |

所有上传数据**按账号隔离**，互不影响，也不会污染共享的 `test_retail.db`。➕ 号面板内同时提供「🗑 新建对话」入口。

---

## 🛠️ 技术栈

| 组件 | 技术选型 |
|------|---------|
| Agent 框架 | ReActAgent + 子代理机制 |
| LLM | DeepSeek / Qwen / OpenAI 兼容接口 |
| 数据库 | SQLite / MySQL / PostgreSQL / ClickHouse |
| RAG 系统 | ChromaDB + Sentence-Transformers + BM25 |
| 数据导入 | pandas + openpyxl（CSV / Excel → SQLite） |
| 文档解析 | pypdf（PDF） + docx2txt（Word） |
| 可视化 | Matplotlib |
| 报告生成 | Jinja2 + FPDF2 + python-pptx |

---

## 📦 项目结构

```
D:\Data Analysis\
├── README.md                      # 项目说明
├── DEVELOPMENT.md                 # 开发文档（计划表、问题解决）
├── demo.py                        # 演示脚本
├── requirements.txt               # Python 依赖
├── .env                           # 环境变量配置
│
└── data_insight/                  # 主项目包
    ├── config.py                  # 配置管理
    ├── main.py                    # CLI 入口
    │
    ├── agents/                    # Agent 层
    │   ├── insight_agent.py       # 主 Agent（编排）
    │   ├── understand_agent.py    # 理解 Agent
    │   ├── analysis_agent.py      # 分析 Agent
    │   └── expression_agent.py    # 表达 Agent
    │
    ├── tools/                     # 工具层
    │   ├── db_connector_tool.py   # 数据库连接（支持SQLite/MySQL/PG/CK）
    │   ├── data_importer.py       # 上传数据导入（CSV/Excel → SQLite，按文件名建表）
    │   ├── sql_generator_tool.py  # NL2SQL
    │   ├── data_analyzer_tool.py  # 数据分析（统计/异常/下钻）
    │   ├── chart_generator_tool.py# 图表生成
    │   ├── report_generator_tool.py# 报告生成
    │   ├── memory_tool.py         # 业务记忆
    │   ├── cache_tool.py          # 查询缓存
    │   └── error_handler.py       # 错误处理
    │
    ├── rag/                       # RAG 知识检索
    │   ├── document_loader.py     # 文档加载（TXT/MD/CSV/JSON/PDF/Word）
    │   ├── text_splitter.py       # 文本分块（语义感知）
    │   ├── embeddings.py          # 向量化（支持HF镜像）
    │   ├── vector_store.py        # 向量存储
    │   ├── retriever.py           # 混合检索（向量+BM25+查询扩展）
    │   ├── reranker.py            # 重排序
    │   └── rag_tool.py            # RAG 工具（带缓存）
    │
    ├── skills/                    # Skills 知识外化
    │   ├── retail_knowledge/
    │   ├── ecommerce_knowledge/
    │   ├── metric_dictionary/
    │   └── analysis_patterns/
    │
    ├── knowledge/                 # 知识库数据
    │   ├── retail_metrics.json
    │   ├── ecommerce_metrics.json
    │   ├── business_rules.json
    │   └── analysis_templates.json
    │
    ├── memory/                    # 持久化存储
    ├── output/                    # 输出目录
    └── tests/                     # 测试
```

---

## 🚀 快速开始

### 1. 安装依赖

```bash
cd "D:\Data Analysis"
pip install -r requirements.txt
```

### 2. 配置环境变量

编辑 `.env` 文件，填入 LLM API Key：

```env
LLM_API_KEY=YOUR_API_KRY
LLM_MODEL_ID=YOUR_MODEL_NAME
LLM_BASE_URL=YOUR_URL

# HuggingFace 镜像（解决下载超时）
HF_ENDPOINT=https://hf-mirror.com

# 数据库类型（默认SQLite，开箱即用）
DEFAULT_DB_TYPE=sqlite
```

### 3. 运行演示

```bash
# 快速演示（展示核心功能）
python demo.py

# 交互模式
python -m data_insight.main
```

### 4. 示例对话

```
👤 你：上个月华东区业绩怎么样？

🤖 RetailSage：
📊 华东区2026年6月业绩概况：
· GMV：1.2亿元，环比下降12%，同比持平
· 订单量：45万单，环比下降8%
· 客单价：267元，环比下降4%

🔍 异常发现：
· A门店：GMV环比下降23%（主要拖累）
· B门店：GMV环比下降15%

需要我进一步分析下降原因吗？

👤 你：是的，帮我分析一下原因

🤖 RetailSage：
🔍 华东区6月业绩下降归因分析：

1. A门店（影响占比45%）
   原因：竞对"XX品牌"6月3日在A门店3公里范围内新开业
   建议：加强会员粘性运营，推出门店专属优惠

2. B门店（影响占比30%）
   原因：5月大促活动透支了6月需求
   建议：控制促销节奏，增加6月独立活动

👤 你：帮我生成一份报告

🤖 RetailSage：
✅ 报告已生成：
· PDF: output/reports/huadong_202606_analysis.pdf
```

---

## 🗄️ 替换为真实数据集

项目默认使用 SQLite 模拟数据（`test_retail.db`），包含 3 张表。你可以替换为自己的真实数据。

### 默认表结构

| 表名 | 字段 | 说明 |
|------|------|------|
| `orders` | order_id, order_date, region, city, store_id, store_name, channel, category, product_name, quantity, order_amount, cost_amount, customer_id, is_member | 订单表 |
| `customers` | customer_id, register_date, region, city, gender, age_group, total_orders, total_amount | 客户表 |
| `stores` | store_id, store_name, region, city, store_type, open_date | 门店表 |

### 方式零：通过 Web 界面上传（零代码，最推荐）

不想改任何配置？启动界面后点击右上角 **➕** 号：

1. 选择「📊 导入数据库（CSV / Excel）」，上传你的 `.csv` / `.xlsx` 文件（可多选）
2. 系统以文件名为表名自动建表（如 `sales 2024.csv` → 表 `sales_2024`，同名表覆盖），并在状态区回显真实表名
3. 回到对话框，直接用自然语言提问，例如「统计 `sales_2024` 的行数」「看看 `sales_2024` 前几行」

> 💡 上传的数据库是**按账号隔离**的独立副本（首次会从基础库 `test_retail.db` 复制一份），不影响他人。
> Agent 会通过 `SELECT name FROM sqlite_master` + `PRAGMA table_info` 自动发现你上传的表结构，无需手改提示词。

若上传的是**业务文档**（产品手册、指标说明、行业报告等），改选「📚 加入知识库（PDF / Word / TXT / MD）」，文档会被解析并纳入 RAG 检索，之后提问时 Agent 会自动引用其中内容。

### 方式一：替换 SQLite 数据库（最简单）

1. 准备你的 SQLite 数据库文件，确保表名和字段名与上表一致（或修改代码适配）
2. 将数据库文件放到 `data_insight/` 目录下
3. 修改 `.env` 中的路径：

```env
DEFAULT_DB_TYPE=sqlite
SQLITE_DATABASE=./data_insight/your_real_data.db
```

### 方式二：连接 MySQL / PostgreSQL / ClickHouse

1. 在 `.env` 中配置数据库连接信息：

```env
# MySQL
DEFAULT_DB_TYPE=mysql
MYSQL_HOST=your-host
MYSQL_PORT=3306
MYSQL_USER=your-user
MYSQL_PASSWORD=your-password
MYSQL_DATABASE=your-database

# 或 PostgreSQL
DEFAULT_DB_TYPE=postgresql
PG_HOST=your-host
PG_USER=your-user
PG_PASSWORD=your-password
PG_DATABASE=your-database
```

2. 确保数据库中的表名和字段名与默认结构一致

### 方式三：自定义表结构

如果你的数据表结构不同，需要修改以下文件：

| 文件 | 修改内容 |
|------|---------|
| `data_insight/agents/insight_agent.py` | 更新系统提示词中的「数据库表结构」部分 |
| `data_insight/main.py` | 更新 `table_schemas` 字典（用于 NL2SQL） |
| `data_insight/knowledge/*.json` | 更新业务知识库（指标定义、业务规则等） |

> 💡 **提示**：即使不修改代码，Agent 也能通过 `PRAGMA table_info` / `DESCRIBE` 自动发现表结构，只是 NL2SQL 的准确率会降低。

### 生成模拟数据（测试用）

如果你想重新生成模拟数据：

```bash
python -m data_insight.tools.create_test_db
```

---

## 🧪 测试

```bash
# 端到端测试
python -m data_insight.tests.test_e2e

# RAG 评测
python -m data_insight.tests.test_ragas

# SQLite 测试
python -m data_insight.tests.test_sqlite
```

---

## 📊 RAGAS 评测结果

| 指标 | 结果 | 目标 |
|------|------|------|
| 检索成功率 | **100%** | ≥ 90% ✅ |
| 上下文相关性 | **94%** | ≥ 80% ✅ |

详细评测报告见 `data_insight/output/reports/ragas_report_*.md`

---


## 📄 许可证

MIT License
