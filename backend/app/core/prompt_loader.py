"""
系统提示词模板加载器
- 让 prompts/*.md 模板文件真正生效（改模板即改 AI 行为）
- 加载顺序：模板文件优先；文件缺失/损坏/为空时回退到代码内默认值
- lru_cache 缓存结果，避免重复读盘
"""
from __future__ import annotations

import functools
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
SYSTEM_PROMPTS_DIR = _PROJECT_ROOT / "prompts"


@functools.lru_cache(maxsize=32)
def load_system_prompt(filename: str, fallback: str) -> str:
    """
    加载系统提示词模板。

    Args:
        filename: prompts/ 下的模板文件名（如 writer_agent.md）
        fallback: 模板不可用时的回退提示词

    Returns:
        模板正文（自动剥离首行 "# 标题"，保留二级结构与正文）；
        文件缺失/读取失败/内容为空时返回 fallback
    """
    path = SYSTEM_PROMPTS_DIR / filename
    try:
        text = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return fallback
    if not text:
        return fallback

    lines = text.splitlines()
    # 剥离首行 "# 标题"，避免冗余标题进入 system prompt
    if lines and lines[0].lstrip().startswith("# "):
        lines = lines[1:]
    result = "\n".join(lines).strip()
    return result or fallback
