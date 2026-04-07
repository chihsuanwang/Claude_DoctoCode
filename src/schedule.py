"""
schedule.py — CCDRA 排程與日期計算

職責：
  - 依契約條款（CCDRASpec）產生付息日序列、可贖回日序列等。
  - 集中所有需要 QuantLib Calendar / Schedule / DayCounter 的日期邏輯。
  - instrument.py 保持零 QL 依賴；日期計算的 QL 依賴由此模組承擔。

設計原則：
  - CCDRAScheduleBuilder 接受 CCDRASpec（純資料），輸出 Python date 列表。
  - 下游模組可直接使用 date 列表，無需再碰 QL 日期物件。

Dependencies:
    QuantLib-Python (ql)
    instrument.py
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

import QuantLib as ql

from .instrument import CCDRASpec


# ---------------------------------------------------------------------------
# 工具函數：Python date ↔ QuantLib Date 轉換
# ---------------------------------------------------------------------------


def _to_ql_date(d: date) -> ql.Date:
    """
    將 Python date 轉換為 QuantLib Date。

    Parameters
    ----------
    d : date
        Python datetime.date 物件。

    Returns
    -------
    ql.Date
        對應的 QuantLib Date 物件。
    """
    return ql.Date(d.day, d.month, d.year)


def _to_py_date(d: ql.Date) -> date:
    """
    將 QuantLib Date 轉換為 Python date。

    Parameters
    ----------
    d : ql.Date
        QuantLib Date 物件。

    Returns
    -------
    date
        對應的 Python datetime.date 物件。
    """
    return date(d.year(), d.month(), d.dayOfMonth())


def _parse_day_count(day_count_str: str) -> ql.DayCounter:
    """
    將契約計息基礎字串轉換為 QuantLib DayCounter 物件。

    Parameters
    ----------
    day_count_str : str
        計息基礎字串，接受 '30/360'、'act/360'、'act/365'（大小寫不拘）。

    Returns
    -------
    ql.DayCounter
        對應的 QuantLib DayCounter。

    Raises
    ------
    ValueError
        若傳入不支援的計息基礎字串。
    """
    mapping: dict[str, ql.DayCounter] = {
        "30/360":       ql.Thirty360(ql.Thirty360.BondBasis),
        "actual/360":   ql.Actual360(),
        "act/360":      ql.Actual360(),
        "actual/365":   ql.Actual365Fixed(),
        "act/365":      ql.Actual365Fixed(),
    }
    key = day_count_str.lower().replace(" ", "")
    if key not in mapping:
        raise ValueError(
            f"不支援的計息基礎：'{day_count_str}'。"
            f"支援項目：{list(mapping.keys())}"
        )
    return mapping[key]


def _parse_frequency(frequency_str: str) -> ql.Frequency:
    """
    將付息頻率字串轉換為 QuantLib Frequency。

    Parameters
    ----------
    frequency_str : str
        頻率字串，接受 'quarterly'、'semiannual'、'annual'（大小寫不拘）。

    Returns
    -------
    ql.Frequency
        對應的 QuantLib Frequency 列舉值。

    Raises
    ------
    ValueError
        若傳入不支援的頻率字串。
    """
    mapping: dict[str, ql.Frequency] = {
        "quarterly":   ql.Quarterly,
        "semiannual":  ql.Semiannual,
        "semi-annual": ql.Semiannual,
        "annual":      ql.Annual,
        "monthly":     ql.Monthly,
    }
    key = frequency_str.lower().strip()
    if key not in mapping:
        raise ValueError(
            f"不支援的付息頻率：'{frequency_str}'。"
            f"支援項目：{list(mapping.keys())}"
        )
    return mapping[key]


# ---------------------------------------------------------------------------
# 輸出資料容器
# ---------------------------------------------------------------------------


@dataclass
class CCDRASchedule:
    """
    CCDRA 完整排程資訊容器（CCDRAScheduleBuilder 的輸出）。

    Attributes
    ----------
    payment_dates : list[date]
        所有計息期付息日（含到期日），依時間正序排列。
        長度 = n_periods。
    call_dates : list[date]
        凍結期結束後的合法贖回日（與付息日相同，但排除凍結期）。
    freeze_end_date : date
        凍結期結束日（= issue_date + freeze_years 年，已做假日調整）。
    accrual_start_dates : list[date]
        各計息期的起算日，長度 = n_periods。
        accrual_start_dates[i] 為 payment_dates[i] 的計息起算日。
    day_count_fractions : list[float]
        各計息期的年化天數比例（e.g., 30/360 每季 ≈ 0.25）。
    n_periods : int
        總計息期數。
    """

    payment_dates: list[date]
    call_dates: list[date]
    freeze_end_date: date
    accrual_start_dates: list[date]
    day_count_fractions: list[float]
    n_periods: int


# ---------------------------------------------------------------------------
# 排程建構器
# ---------------------------------------------------------------------------


class CCDRAScheduleBuilder:
    """
    CCDRA 排程建構器：依 CCDRASpec 產生完整排程資訊。

    使用 QuantLib Schedule 確保日期調整（Modified Following）
    與假日處理的正確性。

    Parameters
    ----------
    spec : CCDRASpec
        CCDRA 契約規格（純資料，來自 instrument.py）。
    calendar : Optional[ql.Calendar]
        交易日曆，預設為 ql.UnitedStates(ql.UnitedStates.GovernmentBond)。

    Examples
    --------
    >>> from datetime import date
    >>> from src.instrument import CCDRASpec
    >>> from src.schedule import CCDRAScheduleBuilder
    >>>
    >>> spec = CCDRASpec(
    ...     issue_date=date(2022, 3, 31),
    ...     maturity_date=date(2027, 3, 31),
    ...     nominal=10_000_000.0,
    ...     coupon_rate=0.0165,
    ...     floor=0.0,
    ...     ceiling=0.0425,
    ...     call_price=100.0,
    ...     freeze_years=1,
    ...     cms_tenor_years=2,
    ...     libor_tenor_months=3,
    ...     payment_frequency='quarterly',
    ...     day_count='30/360',
    ...     currency='USD',
    ...     credit_spread=0.005,
    ... )
    >>> builder = CCDRAScheduleBuilder(spec)
    >>> schedule = builder.build()
    >>> print(schedule.n_periods)            # 20（5年 × 4季）
    >>> print(schedule.payment_dates[0])     # 2022-06-30
    >>> print(schedule.freeze_end_date)      # 2023-03-31
    >>> print(len(schedule.call_dates))      # 16（凍結期後的付息日）
    """

    def __init__(
        self,
        spec: CCDRASpec,
        calendar: Optional[ql.Calendar] = None,
    ) -> None:
        self._spec = spec
        self._calendar = calendar or ql.UnitedStates(
            ql.UnitedStates.GovernmentBond
        )
        self._day_count = _parse_day_count(spec.day_count)
        self._frequency = _parse_frequency(spec.payment_frequency)

    def build(self) -> CCDRASchedule:
        """
        建構完整的 CCDRA 排程，回傳 CCDRASchedule 資料容器。

        步驟：
          1. 以 ql.Schedule 產生所有付息日（含 Modified Following 調整）
          2. 分離計息起算日（accrual start）與付息日（payment date）
          3. 計算凍結期結束日，過濾合法贖回日
          4. 計算各期年化計息天數比例

        Returns
        -------
        CCDRASchedule
            完整排程資訊，包含付息日、贖回日、計息比例等。
        """
        ql_schedule = self._build_ql_schedule()

        # QL Schedule 包含所有日期（含起始日），第一個為 issue_date
        all_dates = [_to_py_date(d) for d in ql_schedule]
        accrual_start_dates = all_dates[:-1]   # 每期計息起算日
        payment_dates = all_dates[1:]           # 每期付息日（含到期日）

        freeze_end = self._compute_freeze_end()

        # 凍結期結束後（不含結束日當天）的付息日才可贖回
        call_dates = [d for d in payment_dates if d > freeze_end]

        dcf = self._compute_day_count_fractions(accrual_start_dates, payment_dates)

        return CCDRASchedule(
            payment_dates=payment_dates,
            call_dates=call_dates,
            freeze_end_date=freeze_end,
            accrual_start_dates=accrual_start_dates,
            day_count_fractions=dcf,
            n_periods=len(payment_dates),
        )

    def _build_ql_schedule(self) -> ql.Schedule:
        """
        使用 QuantLib Schedule 產生付息日序列（含假日調整）。

        採用 Forward generation（從生效日往到期日推進），
        終止日採 Modified Following 調整。

        Returns
        -------
        ql.Schedule
            QuantLib 排程物件，含所有日期（issue_date → maturity_date）。
        """
        effective   = _to_ql_date(self._spec.issue_date)
        termination = _to_ql_date(self._spec.maturity_date)
        tenor       = ql.Period(self._frequency)

        return ql.Schedule(
            effective,
            termination,
            tenor,
            self._calendar,
            ql.ModifiedFollowing,       # 中間日期調整慣例
            ql.ModifiedFollowing,       # 終止日調整慣例
            ql.DateGeneration.Forward,  # 從起始日向前推進
            False,                      # end_of_month = False
        )

    def _compute_freeze_end(self) -> date:
        """
        計算凍結期結束日。

        凍結期結束日 = issue_date + freeze_years 年，
        若落在假日則向後調整至下一個交易日（Following）。

        Returns
        -------
        date
            凍結期結束日（已假日調整）。
        """
        issue_ql = _to_ql_date(self._spec.issue_date)
        freeze_end_ql = self._calendar.advance(
            issue_ql,
            ql.Period(self._spec.freeze_years, ql.Years),
            ql.Following,
        )
        return _to_py_date(freeze_end_ql)

    def _compute_day_count_fractions(
        self,
        start_dates: list[date],
        end_dates: list[date],
    ) -> list[float]:
        """
        計算各計息期的年化天數比例（DayCount Fraction）。

        Parameters
        ----------
        start_dates : list[date]
            各計息期起算日列表。
        end_dates : list[date]
            各計息期終止日（付息日）列表。

        Returns
        -------
        list[float]
            各期年化天數比例（30/360 每季 ≈ 0.25）。
        """
        return [
            self._day_count.yearFraction(
                _to_ql_date(s), _to_ql_date(e)
            )
            for s, e in zip(start_dates, end_dates)
        ]
