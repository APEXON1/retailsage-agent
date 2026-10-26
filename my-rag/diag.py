import os
os.environ["NO_PROXY"] = "localhost,127.0.0.1"

from config import DATA_DIR, DEFAULT_OWNER

# 复用 extractor.py 里真实的解析逻辑（确保测试的就是实际生效的代码）
from extractor import _extract_pdf_text_sorted, _normalize_birth, _fallback_birth_from_text, _calc_age_from_birth

fn = "【AI 智能体研发实习生（Agent Skill 方向）_广州 100-120元_天】邓女士 28年应届生.pdf"
# 兼容迁移前后两种目录结构：优先多账号目录，回退旧版 data/ 根目录
fp = os.path.join(DATA_DIR, DEFAULT_OWNER, fn)
if not os.path.exists(fp):
    fp = os.path.join(DATA_DIR, fn)

text = _extract_pdf_text_sorted(fp)
content = text[:8000]

print("全文长度:", len(content))
print("含 '2002' 位置:", content.find("2002"))
print("含 '出生' 位置:", content.find("出生"))
print("前 200 字:", content[:200])

print("\n--- 出生日期兜底逻辑 ---")
# 模拟 LLM 把 '2002.12' 原样塞进 birth_year（弱模型常见"照抄"行为）
print("normalize('2002.12','未知') ->", _normalize_birth("2002.12", "未知"))
print("normalize('2002','12')      ->", _normalize_birth("2002", "12"))
print("normalize('2002年12月','未知')->", _normalize_birth("2002年12月", "未知"))

# 原文正则兜底（LLM 完全没抽到年份时）
fb_y, fb_m = _fallback_birth_from_text(content)
print("fallback_birth_from_text ->", (fb_y, fb_m))
print("推算年龄 ->", _calc_age_from_birth(fb_y, fb_m))
