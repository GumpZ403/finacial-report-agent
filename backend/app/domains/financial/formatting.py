"""指标值渲染：把库内的原始值按 metric_dict.unit_type 转成“展示形态”

单位约定（方案 §4.4）：
    amount     元        -> 亿元（/1e8，两位小数）
    ratio      小数      -> 百分数（*100，两位小数）
    times      倍/次     -> 两位小数 + 次
    days       天        -> 一位小数 + 天
    per_share  元/股     -> 两位小数 + 元/股
    count      其他      -> 两位小数

注意（方案 §6.3）：报告里的数字必须与白名单做**单位归一**后再比对，
这里输出的就是白名单应记录的展示形态。
"""
from __future__ import annotations


def fmt_value(value, unit_type: str = "") -> str:
    """把指标值按单位类型格式化为可读文本（金额→亿元 / 比率→%）"""
    if value is None:
        return "N/A"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    if unit_type == "amount":
        return f"{v / 1e8:.2f} 亿元"
    if unit_type == "ratio":
        return f"{v * 100:.2f}%"
    if unit_type == "times":
        return f"{v:.2f} 次"
    if unit_type == "days":
        return f"{v:.1f} 天"
    if unit_type == "per_share":
        return f"{v:.2f} 元/股"
    return f"{v:.2f}"
