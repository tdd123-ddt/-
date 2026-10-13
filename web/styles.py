# -*- coding: utf-8 -*-
"""网页样式：配色、卡片、徽章（医疗感、简洁）。"""

# 严重度配色
SEVERITY_COLOR = {
    "高": "#e53e3e",   # 红
    "中": "#dd6b20",   # 橙
    "低": "#d69e2e",   # 黄
}
SEVERITY_BG = {
    "高": "#fff5f5",
    "中": "#fffaf0",
    "低": "#fffff0",
}

# 大类配色（临床/费用/体验）
CATEGORY_COLOR = {
    "临床": "#2b6cb0",  # 蓝
    "费用": "#2f855a",  # 绿
    "体验": "#6b46c1",  # 紫
}


CSS = """
<style>
/* 整体 */
.block-container {padding-top: 2rem; max-width: 1100px;}
h1 {color: #1a202c;}
/* 卡片 */
.card {
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 12px;
    padding: 1rem 1.25rem;
    margin: 0.6rem 0;
    box-shadow: 0 1px 3px rgba(0,0,0,0.06);
}
/* 严重度徽章 */
.badge {
    display: inline-block;
    padding: 0.15rem 0.6rem;
    border-radius: 999px;
    font-size: 0.85rem;
    font-weight: 600;
    color: #fff;
    margin-right: 0.4rem;
}
.badge-cat {
    display: inline-block;
    padding: 0.12rem 0.55rem;
    border-radius: 999px;
    font-size: 0.8rem;
    font-weight: 500;
    background: #edf2f7;
    color: #2d3748;
    margin-right: 0.4rem;
}
/* 痛点卡片左侧色条 */
.pp-card {
    background: #fff;
    border: 1px solid #e2e8f0;
    border-left: 4px solid #ccc;
    border-radius: 10px;
    padding: 0.8rem 1rem;
    margin: 0.6rem 0;
}
.pp-reason {color: #2d3748; font-size: 0.95rem; margin-top: 0.3rem;}
.pp-evidence {color: #718096; font-size: 0.85rem; margin-top: 0.25rem;}
/* 指引卡片（一封信） */
.guidance-card {
    background: #ebf8ff;
    border: 1px solid #bee3f8;
    border-radius: 14px;
    padding: 1.5rem 2rem;
    line-height: 1.8;
    color: #1a202c;
}
.guidance-card h3 {color: #2b6cb0; margin-top: 1.2rem;}
/* 关键事实 */
.kv {font-size: 0.95rem;}
</style>
"""


def inject_css(st):
    st.markdown(CSS, unsafe_allow_html=True)
