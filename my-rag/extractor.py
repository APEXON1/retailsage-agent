# ==================== 🧠 文档读取与 LLM 解析模块（从 main.py 解耦拆分） ====================
"""负责：PDF/TXT/DOCX 文本提取（含 OCR 兜底）、LLM 字段抽取、规则兜底与 JD 智能匹配。
注意：fitz(pymupdf) / pypdf / rapidocr 等在函数内惰性导入，
以保证依赖缺失时 OCR 能优雅降级、不阻塞模块加载（与原始逻辑一致）。"""

import re
import json
import logging
import concurrent.futures
from datetime import datetime

from langchain_core.messages import HumanMessage
from langchain_core.documents import Document as LCDocument
from langchain_community.document_loaders import PyPDFLoader, Docx2txtLoader, TextLoader


# ==================== 🔍 OCR 兜底：应对字体混淆 / 扫描件 PDF ====================
_OCR_ENGINE = None

def ocr_pdf_to_text(filepath: str):
    """将 PDF 逐页渲染为图片并用 RapidOCR 识别，返回拼接文本。
    若 OCR 依赖未安装则返回 None（调用方据此给出友好提示）。"""
    global _OCR_ENGINE
    try:
        import fitz  # pymupdf
        from rapidocr_onnxruntime import RapidOCR
        from PIL import Image
        import numpy as np
        import io
    except ImportError:
        return None

    if _OCR_ENGINE is None:
        _OCR_ENGINE = RapidOCR()
    ocr = _OCR_ENGINE

    doc = fitz.open(filepath)
    pages_text = []
    for page in doc:
        pix = page.get_pixmap(dpi=200)
        image = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
        result, _ = ocr(np.array(image))
        if result:
            pages_text.append("\n".join(line[1] for line in result))
    return "\n".join(pages_text)


def _extract_pdf_text_sorted(filepath: str) -> str:
    """按『视觉阅读顺序』提取 PDF 文本：用 PyMuPDF 的 blocks 排序（先按 y 再按 x），
    解决部分简历 PDF 内容流顺序 ≠ 视觉顺序（如教育经历被排到中后段，导致 LLM 看不到）的问题。
    失败则退化为 PyPDFLoader 按页拼接；两者都失败返回空字符串。"""
    try:
        import fitz
        doc = fitz.open(filepath)
        pages_text = []
        for page in doc:
            # blocks: (x0, y0, x1, y1, text, block_no, block_type)
            blocks = page.get_text("blocks")
            blocks = sorted(blocks, key=lambda b: (round(b[1], 1), round(b[0], 1)))
            pages_text.append("\n".join(b[4] for b in blocks if b[4] and b[4].strip()))
        doc.close()
        text = "\n".join(pages_text).strip()
        if text:
            return text
    except Exception as e:
        logging.warning(f"PyMuPDF 坐标排序提取失败，退化到 PyPDFLoader: {e}")
    # 退化方案：按页顺序拼接
    try:
        docs = PyPDFLoader(filepath).load()
        return "\n".join(d.page_content for d in docs).strip()
    except Exception as e:
        logging.warning(f"PyPDFLoader 退化提取也失败: {e}")
        return ""


# ==================== 🧠 新增：LLM 简历自动解析引擎（增强容错版） ====================
def _parse_age(age_text) -> int | None:
    """从年龄文本中抽取整数年龄，如 "26岁" -> 26；无效返回 None。"""
    if age_text is None:
        return None
    m = re.search(r'\d+', str(age_text))
    return int(m.group()) if m else None

def _normalize_birth(raw_year: str, raw_month: str = "未知"):
    """把 LLM 返回的出生年/月容错归一。
    支持 '2002.12'、'2002-12'、'2002年12月'、纯 '2002'、'2002年' 等组合格式，
    也兼容年、月分别落在两个字段的常规情形。
    返回 (year_str | None, month_str | None)。"""
    raw_year = str(raw_year or "").strip()
    raw_month = str(raw_month or "").strip()

    year = None
    month = None

    # 1) 先从 year 字段里找 19xx/20xx
    ym = re.search(r'(19|20)\d{2}', raw_year)
    if ym:
        year = ym.group(0)
        # year 字段里可能直接带了月份（如 '2002.12' / '2002年12月'）
        mm = re.search(r'[.\-/年]\s*(1[0-2]|\d{1,2})(?:\s*月)?', raw_year[ym.end():])
        if mm:
            v = int(mm.group(1))
            if 1 <= v <= 12:
                month = str(v)
    # 2) year 字段没有月份时，再从 month 字段取合法月份
    if month is None:
        mm = re.search(r'(1[0-2]|\d{1,2})', raw_month)
        if mm:
            v = int(mm.group(1))
            if 1 <= v <= 12:
                month = str(v)
    return year, month


def _fallback_birth_from_text(text: str):
    """LLM 未解析出出生年份时的启发式正则兜底。
    优先在 '出生/生日/生于/出生年月' 等关键词附近找日期；
    没有关键词则取全文中第一个推算年龄落在 [16,65] 的合理日期，
    以年龄合理性过滤掉教育学制、项目起止等无关日期。
    返回 (year_str | None, month_str | None)。"""
    if not text:
        return None, None

    # 1) 关键词附近优先
    for kw in ("出生年月", "出生日期", "出生", "生日", "生于"):
        idx = text.find(kw)
        if idx != -1:
            snippet = text[idx: idx + 40]
            ym = re.search(r'((?:19|20)\d{2})\s*[.\-/年]\s*(0?[1-9]|1[0-2])(?:\s*月)?', snippet)
            if ym:
                return ym.group(1), str(int(ym.group(2)))
            y = re.search(r'((?:19|20)\d{2})\s*年', snippet)
            if y:
                return y.group(1), None

    # 2) 全文第一个合理日期（用年龄窗口过滤非生日）
    now = datetime.now()
    for mm in re.finditer(r'((?:19|20)\d{2})\s*[.\-/年]\s*(0?[1-9]|1[0-2])(?:\s*月)?', text):
        by = int(mm.group(1))
        bm = int(mm.group(2))
        age = now.year - by - (1 if now.month < bm else 0)
        if 16 <= age <= 65:
            return str(by), str(bm)
    # 仅年份（裸年）兜底
    for mm in re.finditer(r'((?:19|20)\d{2})\s*年', text):
        by = int(mm.group(1))
        age = now.year - by
        if 16 <= age <= 65:
            return str(by), None
    return None, None


def _calc_age_from_birth(birth_year, birth_month=None) -> int | None:
    """根据出生年/月推算年龄（动态取当前年份），返回 int 或 None。
    容错：birth_year 可带 '2002.12' 这类组合格式，或传 None。"""
    year, month = _normalize_birth(birth_year, birth_month or "未知")
    if not year:
        return None
    by = int(year)
    now = datetime.now()
    if by <= 0 or by > now.year:
        return None
    age = now.year - by
    # 今年尚未过生日则减 1
    if month and str(month).isdigit():
        bm = int(month)
        if 1 <= bm <= 12 and now.month < bm:
            age -= 1
    return age


# ==================== 🛡️ 档位 B：规则兜底 / 额外字段抽取（不依赖 LLM） ====================
def _rule_gender(text: str):
    """从『性别：男/女』标签中正则抽取性别（高置信、低噪声），仅作 LLM 兜底。"""
    if not text:
        return None
    m = re.search(r'性别\s*[:：]\s*([男女])', text)
    return m.group(1) if m else None


def _rule_university(text: str):
    """从『毕业院校/学校：XX大学/学院』正则抽取院校名（仅作 LLM 兜底）。"""
    if not text:
        return None
    m = re.search(
        r'(?:毕业院校|毕业学校|毕业(?:于|自)|院校|学校|就读)\s*[:：]\s*'
        r'([^\s，,。；;：:]{2,24}?(?:大学|学院|学校|分校))',
        text,
    )
    return m.group(1).strip() if m else None


def _rule_major(text: str):
    """从『专业：XX』正则抽取专业（仅作 LLM 兜底，要求带冒号锚定以降噪）。"""
    if not text:
        return None
    m = re.search(r'(?:专业|主修|所学专业)\s*[:：]\s*([^\s，,。；;：:]{2,24})', text)
    if m:
        val = m.group(1).strip()
        if len(val) >= 2:
            return val
    return None


def _rule_degree(text: str):
    """正则抽取学历层次（大专/本科/硕士/博士/博士后），稳定可靠，适合规则层直接产出。"""
    if not text:
        return None
    m = re.search(r'(?:学历|学位)\s*[:：]\s*(博士后|博士|硕士|本科|大专|中专|高中)', text)
    if m:
        return m.group(1)
    for kw in ('博士后', '博士', '硕士', '本科', '大专', '中专', '高中'):
        if kw in text:
            return kw
    return None


def _rule_work_years(text: str):
    """正则抽取工作年限；优先匹配正文，正文没有再从文件名兜底匹配，『应届』直接返回应届生"""
    if not text:
        return None

    # 1. 优先匹配正文中的明确表达 "X年经验", "工作X年"
    m1 = re.search(r'(\d+)\s*年(?:以上)?\s*(?:相关)?\s*(?:工作)?\s*经验', text)
    if m1:
        return m1.group(1) + "年"

    m2 = re.search(r'工作(?:经验)?\s*(\d+)\s*年', text)
    if m2:
        return m2.group(1) + "年"

    # 2. 优先匹配应届生身份
    if '应届' in text:
        return '应届生'

    # 3. 最后兜底：如果正文啥都没写，再尝试从拼接到头部的【文件名】中提取，如 "xxx 5年.pdf"
    m_file = re.search(r'【文件名】[^\n]*?(\d+)\s*年', text)
    if m_file:
        return m_file.group(1) + "年"

    return None


def _scan_json_objects(text: str) -> list:
    """用花括号配对扫描文本中所有顶层 {...} 片段（状态机，正确处理字符串内的引号与转义）。
    相比非贪婪正则，能在推理模型输出（思考文字夹杂花括号）中准确切出完整 JSON 对象。"""
    objs = []
    depth = 0
    start = -1
    in_str = False
    escape = False
    for i, ch in enumerate(text):
        if in_str:
            if escape:
                escape = False
            elif ch == '\\':
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            if depth > 0:
                depth -= 1
                if depth == 0 and start != -1:
                    objs.append(text[start:i + 1])
                    start = -1
    return objs


def _parse_llm_json(res_text: str) -> dict:
    r"""从大模型返回文本中稳健提取 JSON 对象（高容错版）。
    1. 剥离推理模型的 <think>...</think> 思考块（含被超时截断的未闭合情况）；
    2. 清洗 Markdown 代码围栏（```json / ```）；
    3. 花括号配对扫描出所有 {...} 候选，从最后一个往前尝试解析
       （推理模型的最终 JSON 通常位于思考块之后、文本末尾）；
    4. 解析失败不抛异常，返回标准空字典 {} 并打印 error log。"""
    if not res_text or not str(res_text).strip():
        print("[ERROR] _parse_llm_json: 模型返回为空，无法解析 JSON")
        return {}
    cleaned = str(res_text).strip()
    # 0. 剥离 deepseek-r1 等推理模型的 <think>...</think> 思考块
    cleaned = re.sub(r'<think>.*?</think>', '', cleaned, flags=re.DOTALL)
    # 0.1 未闭合的 <think>（推理被超时截断）：其后没有最终答案，丢弃标签前内容并清理残留标签
    if '<think>' in cleaned and '</think>' not in cleaned:
        cleaned = cleaned.split('<think>')[0]
    cleaned = cleaned.replace('<think>', '').replace('</think>', '').strip()
    # 1. 清洗首尾的 Markdown 围栏符号
    cleaned = re.sub(r'^```[a-zA-Z]*\n?', '', cleaned)
    cleaned = re.sub(r'\n?```\s*$', '', cleaned)
    cleaned = cleaned.strip()
    # 2. 花括号配对扫描所有 {...} 候选，从最后一个往前尝试（最终 JSON 通常在末尾）
    candidates = _scan_json_objects(cleaned)
    for cand in reversed(candidates):
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict):
                return obj
        except json.decoder.JSONDecodeError:
            continue
    print(f"[ERROR] _parse_llm_json: 未能解析出有效 JSON；返回片段: {cleaned[:200]!r}")
    return {}


def _invoke_with_timeout(llm, messages, timeout: int = 300):
    """带超时调用大模型。主超时由 ChatOllama(request_timeout) 在 HTTP 层保证；
    本函数作为兜底，防止个别请求穿透 HTTP 超时导致线程永久阻塞、拖垮整批导入。"""
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        fut = ex.submit(llm.invoke, messages)
        return fut.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        raise TimeoutError(f"LLM 调用超时（>{timeout}s）")
    finally:
        # 不阻塞等待：若请求仍在后台进行，交由进程退出时回收，避免主线程卡死
        ex.shutdown(wait=False)


def extract_resume_info_with_llm(filename: str, filepath: str, llm):
    """利用大模型抽取简历的关键字段。
    返回 (success: bool, message: str, data: dict | None)；不在此处入库，由调用方统一提交以保证事务原子性。"""
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    print(f"   🤖 正在使用 LLM 解析简历字段: {filename}")
    docs = []
    try:
        if filename.endswith('.pdf'):
            # 先探测是否为加密 / 限制复制的 PDF
            try:
                reader = PdfReader(filepath)
                if reader.is_encrypted:
                    try:
                        decrypted = reader.decrypt("")  # 部分平台导出可用空密码解开
                    except Exception:
                        decrypted = 0
                    if decrypted == 0:
                        return False, "PDF 已加密/限制复制，无法读取，请另存为无密码版本后重试", None
            except PdfReadError as e:
                return False, f"PDF 解析失败（可能已损坏或加密）: {e}", None
            # 改用坐标排序提取，让视觉顺序≈文本顺序（教育经历回到前面），且覆盖全文
            pdf_text = _extract_pdf_text_sorted(filepath)
            docs = [LCDocument(page_content=pdf_text)] if pdf_text else []
        elif filename.endswith('.docx') or filename.endswith('.doc'):
            docs = Docx2txtLoader(filepath).load()
        elif filename.endswith('.txt'):
            # 多编码回退，兼容中文 Windows 的 GBK 记事本文件
            docs = []
            for enc in ("utf-8", "gbk", "gb18030"):
                try:
                    docs = TextLoader(filepath, encoding=enc).load()
                    break
                except (UnicodeDecodeError, LookupError):
                    continue
            if not docs:
                return False, "TXT 编码无法识别（已尝试 utf-8 / gbk / gb18030）", None
        else:
            return True, "跳过非文档类型（如网页），未解析", None
    except Exception as e:
        return False, f"加载文件失败: {type(e).__name__} {e}", None

    if not docs:
        return False, "未从文件中提取到任何内容"

    # PDF 内容流顺序 ≠ 视觉顺序，关键信息（如教育经历）常被排到中后段；
    # 但纯 CPU 环境下需控制输入长度，大部分简历在 4000 字范围内，
    # 收紧到 4000 字可显著减少喂给大模型的字符量、加快生成速度
    content = "\n".join([d.page_content for d in docs])[:4000]

    # 空文本 或 乱码/字体混淆（几乎不含中文）→ 尝试 OCR 兜底
    cjk_count = len(re.findall(r'[一-鿿]', content))
    need_ocr = (not content.strip()) or (cjk_count < 3 and len(content.strip()) > 50)
    if need_ocr:
        ocr_text = ocr_pdf_to_text(filepath)
        if ocr_text is None:
            return False, "该文件无有效文字层，且 OCR 依赖未安装（需 pip install pymupdf rapidocr-onnxruntime）", None
        ocr_cjk = len(re.findall(r'[一-鿿]', ocr_text))
        if ocr_text.strip() and ocr_cjk >= 3:
            content = ocr_text[:4000]
        else:
            return False, "提取文本为空/乱码，且 OCR 仍无法识别，请检查文件是否为有效简历", None

    # 将文件名拼接到 content 最头部，确保大模型与下方所有规则兜底（如 _rule_work_years）
    # 都能扫描到文件名中的工作年限、学历等关键信息
    content = f"【文件名】：{filename}\n\n" + content

    prompt = f"""你是一个面向【纯技术研发团队内部招聘】的资深技术 HR 解析助手。请从以下简历文本中提取候选人的基本信息。

必须严格输出为 JSON 格式，包含且仅包含以下 8 个 key："name", "age", "gender", "university", "major", "birth_year", "birth_month", "tags"。

【字段填写规则】
1. 基础字段："name", "age", "gender", "university", "major" 根据简历原文提取，没有则填 "未知"。
2. 出生年月："birth_year" 填纯数字年份（如 "1998"），"birth_month" 填纯数字月份（如 "5"），无则填 "未知"。若只有出生年月无年龄，"age" 填 "未知"，系统会自行推算。
3. "tags"：结构化标签列表（list[str]）。为了满足技术简历的高精度检索，必须从原文中提炼以下 5 个维度的标签，绝不能凭空捏造（无中生有）：
   - 维度 A（所修专业）：【强制要求】即使外层已有 major 字段，也必须将提取到的专业名称（如 "计算机科学与技术"、"软件工程"、"应用数学"）作为一个独立标签放入 tags 中。
   - 维度 B（技术栈与深度）：提取具体的语言/框架（如 "Java", "Vue3", "Laravel", "ThinkPHP", "LangChain"）及深度特征（如 "JVM调优", "微服务架构", "高并发架构", "RAG"）。
   - 维度 C（业务与场景）：提取候选人落地的具体业务场景（如 "AI工作流", "AI智能体研发", "短视频自动化", "SaaS多租户", "大屏可视化", "金融支付"）。
   - 维度 D（工程素养）：提取如 "GitHub开源贡献", "独立开发者", "CI/CD", "DevOps" 等体现工程化能力的标签。
   - 维度 E（基础资质）：提取学历层次（如 "985", "硕士", "本科"）与工作年限（如 "3年经验", "5年经验", "应届"）。

【严格负向约束】
1. 绝对禁止提取"性格开朗"、"抗压能力强"、"熟练使用Word"等无效的主观软技能标签。
2. 绝不允许输出任何除了 JSON 之外的说明性文字、思考过程或多余字符。
3. 绝对禁止将邮箱前缀、QQ号、手机号等数字串误认为是出生年份（例如 1960xxx@qq.com 不代表 1960 年出生）。
4. 注意识别文件名或正文中的"XX年应届生"（如"27年应届生"代表 2027 年毕业，绝不代表 27 岁）。
5. 优先信任简历头部明确标示的年龄数字（如"22岁"），不要擅自根据其他数字进行推算。

【简历文本】：
{content}

【JSON输出】：
"""
    try:
        response = _invoke_with_timeout(llm, [HumanMessage(content=prompt)], timeout=600)
        res_text = response.content if hasattr(response, 'content') else str(response)

        json_data = {"filename": filename, "name": "未知", "age": "未知", "gender": "未知", "university": "未知", "major": "未知", "birth_year": "未知", "tags": [], "degree": "未知", "work_years": "未知"}

        # 稳健解析：清洗 Markdown 围栏 + 非贪婪提取 JSON + 容错（失败返回 {}）
        extracted = _parse_llm_json(res_text)

        if extracted:
            for k in ["name", "age", "gender", "university", "major"]:
                if k in extracted and extracted[k]:
                    json_data[k] = str(extracted[k]).strip()

            # —— 结构化标签提取：list / 逗号分隔字符串 兼容，去重去空 ——
            raw_tags = extracted.get("tags")
            tags_list = []
            if isinstance(raw_tags, (list, tuple)):
                for t in raw_tags:
                    s = str(t).strip()
                    if s and s not in tags_list:
                        tags_list.append(s)
            elif isinstance(raw_tags, str):
                for t in raw_tags.split(","):
                    s = t.strip()
                    if s and s not in tags_list:
                        tags_list.append(s)
            json_data["tags"] = tags_list

            # —— 出生年月提取与年龄推算 ——
            raw_birth_year = str(extracted.get("birth_year", "未知")).strip()
            raw_birth_month = str(extracted.get("birth_month", "未知")).strip()

            # 1) 先尝试用 LLM 返回的年/月（容错归一，支持 '2002.12' 等组合格式）
            birth_year, birth_month = _normalize_birth(raw_birth_year, raw_birth_month)

            # 2) LLM 未解析出年份时，优先用「已知年龄反推出生年份」
            #    若已有有效年龄，直接以当前年份减年龄作为出生年，比正则兜底更可靠；
            #    仅当既没年份也没年龄时，才对原文做正则兜底
            #    （专治"简历无'出生'标签、弱模型不敢认"的情况，如 '邓富美 2002.12'）
            if not birth_year:
                valid_age = _parse_age(json_data["age"])
                if valid_age is not None:
                    birth_year = str(datetime.now().year - valid_age)
                    birth_month = None
                else:
                    fb_year, fb_month = _fallback_birth_from_text(content)
                    birth_year, birth_month = fb_year, fb_month

            # 展示格式：纯年 或 年.月
            if birth_year:
                disp = birth_year
                if birth_month and str(birth_month).isdigit():
                    bm = int(birth_month)
                    if 1 <= bm <= 12:
                        disp = f"{birth_year}.{bm:02d}"
                json_data["birth_year"] = disp
            else:
                json_data["birth_year"] = "未知"

            # 若 LLM 未给出有效年龄，则按出生年月推算
            if _parse_age(json_data["age"]) is None:
                calc_age = _calc_age_from_birth(birth_year, birth_month)
                if calc_age is not None:
                    json_data["age"] = str(calc_age)

        # ============ 🛡️ 档位 B：规则兜底 + 额外字段抽取（与 LLM 是否解析成功无关） ============
        # 仅当 LLM 返回「未知」时用正则回填，避免覆盖已给出的可靠值
        if json_data.get("gender", "未知") in ("未知", "", None):
            g = _rule_gender(content)
            if g:
                json_data["gender"] = g
        if json_data.get("university", "未知") in ("未知", "", None):
            u = _rule_university(content)
            if u:
                json_data["university"] = u
        if json_data.get("major", "未知") in ("未知", "", None):
            mj = _rule_major(content)
            if mj:
                json_data["major"] = mj

        # 额外抽取（规则层稳定可靠）：学历层次 / 工作年限
        degree = _rule_degree(content)
        work_years = _rule_work_years(content)
        json_data["degree"] = degree or "未知"
        json_data["work_years"] = work_years or "未知"

        # 学历层次并入 tags，增强既有「标签检索」（如搜 “硕士” 可命中对应简历）
        if degree and degree != "未知":
            _tags = json_data["tags"]
            if isinstance(_tags, list) and degree not in _tags:
                _tags.append(degree)

        # 专业同步并入 tags，并清理大模型误抄提示词例子产生的脏数据
        _major = json_data.get("major")
        if _major and _major != "未知":
            _tags = json_data["tags"]
            if isinstance(_tags, list):
                if _major not in _tags:
                    _tags.append(_major)
                if _major != "计算机科学与技术" and "计算机科学与技术" in _tags:
                    _tags.remove("计算机科学与技术")

        meaningful = any(json_data[k] != "未知" for k in ["name", "age", "gender", "university", "major"])
        if not meaningful:
            return False, "已解析入库，但所有字段均为「未知」，疑似非简历或模板异常", json_data
        return True, "解析入库成功", json_data
    except Exception as e:
        return False, f"LLM 解析或 JSON 转换失败: {e}", None


# ==================== 🤝 JD 智能匹配（不依赖 RAG 索引，直接比对原文） ====================
def evaluate_resume_against_jd(resume_text: str, jd_text: str, llm) -> dict:
    """将候选人简历文本与 HR 提供的 JD 一并交给大模型，从「学历要求」「技能匹配」「项目经验」
    三个维度综合比对，并输出结构化 JSON 评估结果。
    复用 _parse_llm_json 的防崩溃逻辑做 JSON 容错解析，始终返回含 score/pros/cons 的字典。"""
    prompt = f"""
你是一个资深的 HR 招聘匹配专家。请综合比对【候选人简历】与【岗位 JD】，从以下三个维度进行专业评估：
1. 学历要求：候选人的学历层次、毕业院校是否满足 JD 的学历门槛；
2. 技能匹配：候选人的核心技能、工具栈与 JD 要求的技能契合度；
3. 项目经验：候选人的项目 / 工作经历与 JD 职责的匹配程度与深度。

必须严格输出为 JSON 格式，包含且仅包含以下 3 个 key：
- "score"：0-100 的整数，表示综合匹配分（越高越匹配）；若无相关信息，应给较低分数而非随意给满分。
- "pros"：长度为 3 的字符串数组，列出 3 个匹配的优势点（每条简洁具体、避免空泛）。
- "cons"：字符串数组（1-3 条），列出候选人相对 JD 欠缺的部分；若无欠缺则为空数组 []。
绝不允许输出任何除了 JSON 之外的说明性文字！

【候选人简历】：
{resume_text}

【岗位 JD】：
{jd_text}

【JSON输出】：
"""
    try:
        res = _invoke_with_timeout(llm, [HumanMessage(content=prompt)], timeout=300)
        res_text = getattr(res, "content", "") or ""
    except Exception as e:
        logging.error(f"JD 匹配调用大模型失败: {e}")
        return {"score": None, "pros": [], "cons": []}

    # 复用 Task 1.1 的防崩溃 JSON 解析逻辑
    extracted = _parse_llm_json(res_text)

    # 归一化为稳定结构，缺字段时给安全默认值，避免上层渲染崩溃
    result = {"score": None, "pros": [], "cons": []}

    score = extracted.get("score")
    if score is not None:
        try:
            score = int(score)
            score = max(0, min(100, score))   # 钳制到 0-100
        except (ValueError, TypeError):
            score = None
    result["score"] = score

    pros_raw = extracted.get("pros")
    if isinstance(pros_raw, list):
        result["pros"] = [str(p).strip() for p in pros_raw if str(p).strip()][:3]
    elif isinstance(pros_raw, str) and pros_raw.strip():
        result["pros"] = [pros_raw.strip()]

    cons_raw = extracted.get("cons")
    if isinstance(cons_raw, list):
        result["cons"] = [str(c).strip() for c in cons_raw if str(c).strip()]
    elif isinstance(cons_raw, str) and cons_raw.strip():
        result["cons"] = [cons_raw.strip()]

    return result
