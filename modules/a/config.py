# -*- coding: utf-8 -*-
"""
模块 a · 配置加载

优先级：环境变量 > 项目根目录 `.env` 文件 > 默认值。
这样用户只需在项目根目录放一个 `.env` 文件（见 `.env.example`），
不用每次手敲 export。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional


def _project_root() -> Path:
    # modules/a/config.py -> 项目根目录（上两级）
    return Path(__file__).resolve().parent.parent.parent


def load_dotenv() -> None:
    """把项目根目录 .env 的 KEY=VALUE 读进环境变量（已存在的环境变量优先）。"""
    env_file = _project_root() / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def api_config() -> Dict[str, Optional[str]]:
    """返回 LLM 抽取所需的配置。"""
    load_dotenv()
    return {
        "api_key": os.environ.get("ANTHROPIC_API_KEY"),
        "base_url": os.environ.get("ANTHROPIC_BASE_URL") or "https://api.deepseek.com/anthropic",
        "model": os.environ.get("ANTHROPIC_MODEL") or "deepseek-chat",
    }
