"""指标值的单位语义与展示形态（唯一实现）

单位约定（方案 §4.4）：
    amount     元        -> 亿元（/1e8，两位小数）
    ratio      小数      -> 百分数（*100，两位小数）
    times      倍/次     -> 两位小数 + 次（周转率类）
    multiple   —         -> 两位小数 + 倍（流动比率 / 速动比率 / 利息保障倍数）
    days       天        -> 一位小数 + 天
    per_share  元/股     -> 两位小数 + 元/股
    count      人        -> 整数 + 人

关于 multiple：`metric_dict.unit_type` 只有 6 个取值，没有 multiple。
展示层额外保留一个 multiple 只为区分"次"（周转率）与"倍"（倍数），
**不要用它去写库**——落库单位仍然只有 6 种。

方案 §6.3：报告里的数字必须与白名单做**单位归一**后再比对，
本模块输出的就是白名单应记录的展示形态。所有展示格式化必须走这里，不要在别处再实现一份。
"""
from __future__ import annotations

# unit_type -> 落库用的 unit 标记（写库唯一来源）
UNIT_BY_TYPE: dict[str, str] = {
    "amount": "CNY",
    "ratio": "ratio",
    "times": "times",
    "days": "days",
    "per_share": "per_share",
    "count": "count",
}
UNIT_UNKNOWN = "unknown"

# unit_type -> 展示后缀
_SUFFIX: dict[str, str] = {
    "amount": "亿元",
    "ratio": "%",
    "times": "次",
    "multiple": "倍",
    "days": "天",
    "per_share": "元/股",
    "count": "人",
}


def fmt_value(value, unit_type: str = "") -> str:
    """把库内原始值按单位类型转成展示形态（金额→亿元 / 比率→%）"""
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
    if unit_type == "count":
        return f"{v:,.0f} 人"
    if unit_type in _SUFFIX:
        return f"{v:.2f} {_SUFFIX[unit_type]}" if unit_type != "days" else f"{v:.1f} 天"
    return f"{v:.2f}"


def fmt_change(delta, unit_type: str = "") -> str:
    """变化幅度的展示：比率类用百分点，其余用百分比"""
    try:
        d = float(delta)
    except (TypeError, ValueError):
        return str(delta)
    if unit_type == "ratio":
        return f"{d * 100:+.2f} 个百分点"
    return f"{d * 100:+.1f}%"
