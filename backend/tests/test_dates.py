"""财报日历（dates.py）：期次窗口与披露节奏

这两个函数决定了"入库保留多久"和"还需要不需要再拉"，错一天就会漏数据或多拉。
"""
from datetime import date

import pytest

from app.domains.financial.dates import latest_disclosed_period, years_ago


class TestYearsAgo:
    def test_simple(self):
        assert years_ago(10, date(2026, 10, 17)) == date(2016, 10, 17)

    def test_leap_day_falls_back_to_28(self):
        # 2024-02-29 往前 1 年 → 2023-02-28（不是报错）
        assert years_ago(1, date(2024, 2, 29)) == date(2023, 2, 28)

    def test_zero_is_today(self):
        assert years_ago(0, date(2026, 10, 17)) == date(2026, 10, 17)


class TestLatestDisclosedPeriod:
    @pytest.mark.parametrize("today,expected", [
        # 三季报 10/31 之后
        (date(2026, 10, 31), "2026Q3"),
        (date(2026, 12, 31), "2026Q3"),
        # 半年报 8/31 ~ 10/30
        (date(2026, 8, 31), "2026H1"),
        (date(2026, 10, 30), "2026H1"),
        # 一季报 4/30 ~ 8/30
        (date(2026, 4, 30), "2026Q1"),
        (date(2026, 8, 30), "2026Q1"),
        # 1/1 ~ 4/29：年报可能还没出，只认上一年三季报
        (date(2026, 1, 15), "2025Q3"),
        (date(2026, 4, 29), "2025Q3"),
    ])
    def test_boundaries(self, today, expected):
        assert latest_disclosed_period(today) == expected

    def test_never_returns_current_year_annual(self):
        """任何时点都不应给出 "本年 FY"（年报次年 4/30 才截止，1~4 月出没出不确定）"""
        for month in range(1, 13):
            for day in (1, 15, 28):
                got = latest_disclosed_period(date(2026, month, day))
                assert not got.endswith("FY")
