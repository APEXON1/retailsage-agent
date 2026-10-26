"""
数据文件导入器 - 把用户上传的 CSV/Excel 导入 SQLite 数据库

供 Web 界面「上传 → 导入数据库」链路使用：
- 读取 CSV / Excel(.xlsx) 为 DataFrame
- 以文件名为表名（清洗为合法标识符）写入目标 SQLite，同名表覆盖

同时提供文件类型判定，供上传入口按模式路由（数据表 / 知识文档）。
"""

import logging
import re
import time
from pathlib import Path
from typing import List, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

# 可作为「数据表」导入的扩展名（.xls 需要 xlrd，未安装，故不支持）
DATA_EXTS = {".csv", ".xlsx"}

# 可作为「知识文档」纳入 RAG 的扩展名
KNOWLEDGE_EXTS = {".txt", ".md", ".csv", ".json", ".pdf", ".docx"}


def is_data_file(path: str) -> bool:
    """是否为可导入数据库的数据文件。"""
    return Path(path).suffix.lower() in DATA_EXTS


def is_knowledge_file(path: str) -> bool:
    """是否为可纳入知识库的文档文件。"""
    return Path(path).suffix.lower() in KNOWLEDGE_EXTS


def sanitize_table_name(filename: str) -> str:
    """把文件名清洗成合法的 SQLite 表名（仅 ASCII 字母/数字/下划线）。

    中文或特殊字符文件名会被替换为下划线；若清洗后为空则退回时间戳命名，
    以保证表名一定可用。真实表名会在上传结果里回显给用户。
    """
    stem = Path(filename).stem
    name = re.sub(r"[^0-9A-Za-z_]+", "_", stem).strip("_")
    name = re.sub(r"_+", "_", name)          # 折叠多余下划线
    if not name:
        name = f"imported_{int(time.time())}"
    elif name[0].isdigit():
        name = f"t_{name}"
    return name[:64]                          # 保守截断，避免超长标识符


def read_table_file(path: str) -> pd.DataFrame:
    """读取 CSV / Excel 为 DataFrame。CSV 自动尝试多种中文/西文编码。"""
    ext = Path(path).suffix.lower()
    if ext == ".csv":
        last_err = None
        for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030", "latin1"):
            try:
                return pd.read_csv(path, encoding=enc)
            except UnicodeDecodeError as e:
                last_err = e
                continue
        raise ValueError(f"CSV 编码无法识别: {last_err}")
    if ext == ".xlsx":
        return pd.read_excel(path, engine="openpyxl")
    if ext == ".xls":
        raise ValueError("暂不支持旧版 .xls，请另存为 .xlsx 或 .csv 后再上传")
    raise ValueError(
        f"不支持的数据文件格式: {ext}（仅支持 {', '.join(sorted(DATA_EXTS))}）"
    )


def import_to_sqlite(df: pd.DataFrame, table_name: str, db_path: str) -> Tuple[int, List[str]]:
    """把 DataFrame 写入 SQLite（同名表覆盖）。返回 (行数, 列名列表)。"""
    from sqlalchemy import create_engine

    if df is None or df.empty:
        raise ValueError("文件内容为空，没有可导入的数据")

    # 清洗列名：去首尾空格，空列名补占位，避免建表失败
    df = df.copy()
    cleaned_cols = []
    for i, c in enumerate(df.columns):
        c = str(c).strip() or f"col_{i + 1}"
        cleaned_cols.append(c)
    df.columns = cleaned_cols

    engine = create_engine(f"sqlite:///{db_path}")
    try:
        df.to_sql(table_name, engine, if_exists="replace", index=False)
    finally:
        engine.dispose()

    logger.info(
        "导入数据表 %s: %d 行 %d 列 -> %s",
        table_name, len(df), len(df.columns), db_path,
    )
    return len(df), list(df.columns)
