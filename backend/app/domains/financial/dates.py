"""财报日历：期次窗口与披露节奏（纯函数，无副作用、无 IO）

两个概念别混：
- **期次窗口**（`years_ago`）：入库保留最近 N 年数据，N 来自 `Settings.PERIOD_WINDOW_YEARS`。
  按日期判定而不是按日历年，否则每年 1 月窗口会悄悄退化成"9 年出头"。
- **最新已披露期次**（`latest_disclosed_period`）：按法定披露截止日推算"此刻应该已经能看到的最新一期"，
  增量拉取靠它判断某家公司还能不能拉到新东西。
"""
from datetime import date


def years_ago(years: int, today: date | None = None) -> date:
    """今天往前推 N 年（2 月 29 日 → 2 月 28 日，避免 ValueError）"""
    d = today or date.today()
    n = int(years)
    try:
        return d.replace(year=d.year - n)
    except ValueError:
        return d.replace(year=d.year - n, day=28)


_PERIOD_END = {"Q1": (3, 31), "H1": (6, 30), "Q3": (9, 30), "FY": (12, 31)}


def period_end_of(period: str) -> str:
    """'2026H1' → '2026-06-30'

    用来比较"库里最新期次是否已经追平披露日历"——不能直接比 period 字符串：
    "2026FY" < "2026H1" < "2026Q1" < "2026Q3" 是按字母序，不是时间序。
    """
    year, suffix = str(period)[:4], str(period)[4:]
    month, day = _PERIOD_END.get(suffix, (12, 31))
    return f"{year}-{month:02d}-{day:02d}"


def latest_disclosed_period(today: date | None = None) -> str:
    """按披露截止日推算"最新已披露期次"，返回与 `financial_periods.period` 同格式

    法定截止日（A 股）：
    - 一季报 4/30
    - 半年报 8/31
    - 三季报 10/31
    - 年报 次年 4/30（实际 1~4 月任意时点都可能出）

    1/1~4/29 这段只认"上一年三季报"——年报可能还没出，宁可保守（多拉一次不算错，
    但据"年报已出"跳过就会漏数据）。
    """
    d = today or date.today()
    if d >= date(d.year, 10, 31):
        return f"{d.year}Q3"
    if d >= date(d.year, 8, 31):
        return f"{d.year}H1"
    if d >= date(d.year, 4, 30):
        return f"{d.year}Q1"
    return f"{d.year - 1}Q3"
