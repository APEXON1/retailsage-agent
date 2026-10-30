"""RetailSage 三应用共享 UI 主题（Gradio）。

单一事实来源：设计令牌取自 portal/static/style.css 的 :root / [data-theme="dark"]，
门户（FastAPI）、洞察（Gradio）、知识库（Gradio）三端共用同一套靛蓝主色 + 日夜配色，
让三个独立进程看起来是同一个产品。

用法：
    from rs_theme import build_theme, THEME_CSS
    with gr.Blocks(theme=build_theme()) as demo:
        ...
    demo.launch(css=THEME_CSS)          # 单机
    # 门户托管：gr.mount_gradio_app(app, demo, css=THEME_CSS + 各应用专有 CSS, ...)

约定：
- 颜色一律走 CSS 变量 --rs-*，浅色定义在 .gradio-container，夜间在 .dark 下覆盖。
  Gradio 夜间模式会在 <html> 上加 .dark 类，故 `.dark .gradio-container` 生效。
- 各应用专有样式（如洞察的容器宽度、知识库的侧栏/表格）不要塞进这里，
  在各自模块里追加，再与 THEME_CSS 拼接。
"""

from __future__ import annotations

import gradio as gr

# ==================== 设计令牌（与 portal/static/style.css 对齐）====================
ACCENT = "#4f46e5"            # 靛蓝主色（= Tailwind indigo-600）
ACCENT_HOVER = "#4338ca"
FONT_STACK = [
    "-apple-system", "BlinkMacSystemFont", "Segoe UI", "PingFang SC",
    "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans SC", "sans-serif",
]
FONT_MONO = ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"]


def build_theme() -> gr.Theme:
    """构建统一的 Gradio 主题（含浅色 / 夜间两套，由 Gradio 依 primary_hue 自动生成暗色变体）。

    primary_hue 用命名调色板 "indigo"（其 indigo-600 恰为 #4f46e5）——gr.Theme 不接受裸 hex。
    关键品牌色（发送按钮 / 用户气泡 / chip 悬停）再由 THEME_CSS 以 !important 精确锁定为 #4f46e5。
    """
    return gr.Theme(
        primary_hue="indigo",
        secondary_hue="indigo",
        neutral_hue="slate",
        font=FONT_STACK,
        font_mono=FONT_MONO,
        radius_size="lg",
        spacing_size="md",
        text_size="md",
    )


# ==================== 共享样式 ====================
# 说明：这里是三端通用的「令牌 + 组件外观」。浅色值与改造前洞察界面基本一致，
# 夜间值取自门户 [data-theme="dark"]，因此日夜主题同步后两端都能正确变色。
THEME_CSS = """
/* ---------- 设计令牌：浅色 ---------- */
.gradio-container {
    --rs-bg: #f6f7f9;
    --rs-panel: #ffffff;
    --rs-panel-2: #f3f4f6;
    --rs-border: #e4e6eb;
    --rs-border-strong: #d3d6dd;
    --rs-text: #1a1d21;
    --rs-text-dim: #6b7280;
    --rs-text-faint: #9aa1ac;
    --rs-accent: #4f46e5;
    --rs-accent-hover: #4338ca;
    --rs-accent-soft: #eef2ff;
    --rs-accent-fill: #4f46e5;
    --rs-shadow: 0 1px 3px rgba(0, 0, 0, .05);
    --rs-shadow-lift: 0 4px 20px rgba(0, 0, 0, .08);
    position: relative !important;
    background: var(--rs-bg) !important;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
                 "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans SC", sans-serif !important;
}

/* ---------- 设计令牌：夜间（Gradio 在 <html> 上加 .dark）---------- */
.dark .gradio-container,
.gradio-container.dark {
    --rs-bg: #0f1115;
    --rs-panel: #171a20;
    --rs-panel-2: #1e2229;
    --rs-border: #262b33;
    --rs-border-strong: #333a45;
    --rs-text: #e8eaed;
    --rs-text-dim: #9aa1ac;
    --rs-text-faint: #6b7280;
    --rs-accent: #818cf8;
    --rs-accent-hover: #a5b4fc;
    --rs-accent-soft: #1e2235;
    --rs-accent-fill: #4f46e5;
    --rs-shadow: 0 1px 3px rgba(0, 0, 0, .4);
    --rs-shadow-lift: 0 4px 20px rgba(0, 0, 0, .5);
}

/* ---------- 主面板 ---------- */
.main-panel {
    border: none !important;
    box-shadow: none !important;
    background: transparent !important;
}

/* ---------- 顶部标题栏 ---------- */
.header-row {
    display: flex !important;
    flex-direction: row !important;
    align-items: flex-start !important;
    justify-content: flex-start !important;
    padding: 20px 0 8px 8px !important;
    gap: 12px !important;
}
.welcome-title h1 {
    font-size: 26px !important;
    font-weight: 600 !important;
    color: var(--rs-text) !important;
    margin: 0 !important;
    text-align: left !important;
    line-height: 1.2 !important;
}
.welcome-subtitle p {
    font-size: 14px !important;
    color: var(--rs-text-dim) !important;
    margin: 4px 0 0 0 !important;
    text-align: left !important;
    line-height: 1.4 !important;
}
.user-tag {
    font-size: 12px !important;
    color: var(--rs-text-faint) !important;
    word-break: break-all !important;
    margin-top: 4px !important;
}

/* ---------- 右上角图标按钮（洞察 + 号；通用绝对定位）---------- */
.new-chat-icon {
    position: absolute !important;
    top: 24px !important;
    right: 24px !important;
    z-index: 1000 !important;
    border-radius: 8px !important;
    width: 24px !important; height: 24px !important;
    min-width: 24px !important; max-width: 24px !important;
    min-height: 24px !important; max-height: 24px !important;
    padding: 0 !important;
    font-size: 14px !important; line-height: 1 !important;
    background: var(--rs-panel) !important;
    border: 1px solid var(--rs-border) !important;
    box-shadow: var(--rs-shadow) !important;
    transition: all 0.15s ease !important;
    display: flex !important; align-items: center !important; justify-content: center !important;
}
.new-chat-icon:hover {
    background: var(--rs-accent-soft) !important;
    border-color: var(--rs-accent) !important;
    color: var(--rs-accent) !important;
}
.new-chat-icon button {
    border-radius: 8px !important;
    width: 24px !important; height: 24px !important;
    padding: 0 !important;
    display: flex !important; align-items: center !important; justify-content: center !important;
}

/* ---------- 对话区域 ---------- */
.chatbot {
    border: none !important;
    border-radius: 16px !important;
    background: var(--rs-panel) !important;
    box-shadow: var(--rs-shadow) !important;
    min-height: 420px !important;
}
.chatbot .message-row { padding: 8px 16px !important; }
/* 用户气泡：靛蓝底白字 */
.chatbot .message.user,
.chatbot .user {
    background: var(--rs-accent-fill) !important;
    border-color: var(--rs-accent-fill) !important;
    color: #ffffff !important;
    border-radius: 16px 16px 4px 16px !important;
}
.chatbot .message.user .prose,
.chatbot .message.user .prose *,
.chatbot .message.user .flex-wrap,
.chatbot .message.user .flex-wrap *,
.chatbot .user .prose,
.chatbot .user .prose *,
.chatbot .user .flex-wrap,
.chatbot .user .flex-wrap * {
    color: #ffffff !important;
    text-align: left !important;
}
/* 机器人气泡 */
.chatbot .message.bot,
.chatbot .bot {
    background: var(--rs-panel-2) !important;
    color: var(--rs-text) !important;
    border-radius: 16px 16px 16px 4px !important;
}

/* ---------- 底部输入区 ---------- */
.input-area {
    background: var(--rs-panel) !important;
    border: 1px solid var(--rs-border) !important;
    border-radius: 20px !important;
    box-shadow: var(--rs-shadow-lift) !important;
    padding: 12px 16px !important;
    margin-top: 16px !important;
    transition: all 0.2s ease !important;
}
.input-area:focus-within {
    border-color: var(--rs-accent) !important;
    box-shadow: 0 4px 24px rgba(79, 70, 229, 0.15) !important;
}
.input-box textarea {
    border: none !important; outline: none !important; box-shadow: none !important;
    font-size: 15px !important; line-height: 1.5 !important; resize: none !important;
    background: transparent !important;
    color: var(--rs-text) !important;
}
.input-box textarea:focus { border: none !important; outline: none !important; box-shadow: none !important; }
.send-btn {
    border-radius: 12px !important;
    background: var(--rs-accent-fill) !important;
    color: #ffffff !important;
    font-weight: 600 !important;
    transition: all 0.15s ease !important;
}
.send-btn:hover {
    background: var(--rs-accent-hover) !important;
    box-shadow: 0 2px 8px rgba(79, 70, 229, 0.3) !important;
}

/* ---------- 推荐问题 chip ---------- */
.suggestions-row {
    display: flex !important; align-items: center !important; gap: 8px !important;
    margin-top: 10px !important; padding-left: 4px !important; flex-wrap: wrap !important;
}
.suggestion-label { color: var(--rs-text-faint) !important; font-size: 13px !important; white-space: nowrap !important; }
.suggestion-chip {
    background: var(--rs-panel) !important;
    border: 1px solid var(--rs-border) !important;
    border-radius: 999px !important;
    color: var(--rs-text-dim) !important;
    font-size: 13px !important; padding: 4px 12px !important;
    cursor: pointer !important; transition: all 0.15s ease !important; white-space: nowrap !important;
}
.suggestion-chip:hover {
    border-color: var(--rs-accent) !important;
    color: var(--rs-accent) !important;
    background: var(--rs-accent-soft) !important;
}

/* ---------- 底部信息 / 门户链接 / 页脚 ---------- */
.bottom-info { margin-top: 8px !important; }
.capabilities { text-align: center !important; color: var(--rs-text-faint) !important; font-size: 12px !important; margin-top: 8px !important; }
.portal-link { display: block !important; text-align: center !important; font-size: 12px !important; margin-top: 6px !important; color: var(--rs-text-faint) !important; }
.portal-link a { color: var(--rs-text-dim) !important; text-decoration: none !important; }
.portal-link a:hover { color: var(--rs-accent) !important; }
.footer-info { text-align: center !important; color: var(--rs-text-faint) !important; font-size: 12px !important; margin-top: 16px !important; }

/* ---------- 思考中动画 / 流式文本 ---------- */
.thinking-bubble { display: flex !important; align-items: center !important; gap: 8px !important; }
.thinking-bubble p { margin: 0 !important; }
.thinking-text { color: var(--rs-text-dim) !important; font-size: 14px !important; }
.stream-text {
    margin-top: 8px !important; white-space: pre-wrap !important; word-break: break-word !important;
    color: var(--rs-text) !important; font-size: 14px !important; line-height: 1.6 !important;
}
.thinking-spinner {
    display: inline-block !important; width: 14px !important; height: 14px !important;
    border: 2px solid var(--rs-border) !important; border-top-color: var(--rs-accent) !important;
    border-radius: 50% !important; animation: spin 1s linear infinite !important; vertical-align: middle !important;
}
@keyframes spin { to { transform: rotate(360deg); } }
.time-display { text-align: center !important; color: var(--rs-text-dim) !important; font-size: 12px !important; margin-top: 4px !important; min-height: 18px !important; }

/* ---------- 上传面板 ---------- */
.upload-panel {
    background: var(--rs-panel) !important;
    border: 1px solid var(--rs-border) !important;
    border-radius: 16px !important;
    box-shadow: var(--rs-shadow-lift) !important;
    padding: 14px 16px !important;
    margin: 12px 0 4px 0 !important;
    gap: 8px !important;
}
.upload-title { font-size: 15px !important; font-weight: 600 !important; color: var(--rs-text) !important; }
.upload-status { font-size: 13px !important; color: var(--rs-text-dim) !important; line-height: 1.6 !important; }
.upload-status p { margin: 2px 0 !important; }
.upload-actions { margin-top: 4px !important; }
"""
