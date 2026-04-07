"""
instrument.py — CCDRA 商品契約規格（純資料容器）

設計原則：
  - 此 dataclass 只儲存靜態契約條款，不包含任何業務邏輯方法。
  - 不 import QuantLib，確保在任何環境下均可單純建立與序列化。
  - 排程計算（payment_dates, call_dates 等）由 schedule.py 負責。

被以下模組 import：
  schedule.py, mc_engine.py, main.py
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass
class CCDRASpec:
    """
    可贖回 CMS 連結每日區間計息債券（CCDRA）契約規格。

    此物件為各模組的唯一資料來源（Single Source of Truth），
    建立後視為不可變（所有欄位應在初始化時設定完畢）。

    Attributes
    ----------
    issue_date : date
        發行日（生效日）。
    maturity_date : date
        到期日。
    nominal : float
        名目本金，以計價幣別面額表示（e.g., 10_000_000.0）。
    coupon_rate : float
        票面利率，以小數表示（e.g., 0.0165 = 1.65%）。
    floor : float
        區間計息下限利率，以小數表示（e.g., 0.0 = 0%）。
    ceiling : float
        區間計息上限利率，以小數表示（e.g., 0.0425 = 4.25%）。
    call_price : float
        贖回價格，以面額百分比表示（e.g., 100.0 = par）。
    freeze_years : int
        凍結期年數，凍結期內發行人不得行使贖回權。
    cms_tenor_years : int
        CMS 利率參考年期（e.g., 2 = 2Y CMS，對應 USSW2）。
    libor_tenor_months : int
        LMM 基礎 LIBOR 指標期數（e.g., 3 = Libor 3M）。
    payment_frequency : str
        付息頻率，接受 'quarterly' / 'semiannual' / 'annual'。
    day_count : str
        計息基礎，接受 '30/360' / 'act/360' / 'act/365'。
    currency : str
        計價幣別（e.g., 'USD'）。
    credit_spread : float
        信用利差，以小數表示，用於折現調整（e.g., 0.005 = 0.5%）。
    last_fixing : Optional[float]
        評價日前最近一次 CMS 指標利率報價。
        None 代表由外部資料查詢（非契約條款，可於評價時傳入）。
    in_range_days : int
        評價日距上一付息日已累積落入區間的天數（用於當期應計計算）。

    Examples
    --------
    文件範例契約（第五章）：

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
    ...     last_fixing=0.0255,
    ...     in_range_days=0,
    ... )
    """

    issue_date: date
    maturity_date: date
    nominal: float
    coupon_rate: float
    floor: float
    ceiling: float
    call_price: float
    freeze_years: int
    cms_tenor_years: int
    libor_tenor_months: int
    payment_frequency: str
    day_count: str
    currency: str
    credit_spread: float
    last_fixing: Optional[float] = None
    in_range_days: int = 0
