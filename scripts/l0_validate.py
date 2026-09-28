# -*- coding: utf-8 -*-
"""
L0 数据质量校验（兼容入口）

校验逻辑统一在数据层：`app/data/quality.py`（方案 v1.1 §8.3）。
本脚本只做参数解析与结果打印，避免此前"scripts 与数据层两份 L0 实现"的重复。

用法：
    .venv/Scripts/python.exe scripts/l0_validate.py                 # 全库校验并写日志
    .venv/Scripts/python.exe scripts/l0_validate.py --no-mark        # 不标记 is_estimated
    .venv/Scripts/python.exe scripts/l0_validate.py --keep-log       # 保留历史日志
    .venv/Scripts/python.exe scripts/l0_validate.py --company 300750 # 只校验单家公司
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.data.quality import run_l0          # noqa: E402
from app.data.repository import get_db       # noqa: E402


def main():
    argv = sys.argv[1:]
    picks: list[str] = []
    if "--company" in argv:
        i = argv.index("--company")
        picks = [a for a in argv[i + 1:] if not a.startswith("--")]
    result = run_l0(
        db=get_db(),
        companies=picks or None,
        mark_estimated="--no-mark" not in argv,
        clear_log="--keep-log" not in argv,
    )
    print(json.dumps(
        {k: v for k, v in result.items() if k != "coverage_by_template_metric"},
        ensure_ascii=False, indent=1,
    ))


if __name__ == "__main__":
    main()
