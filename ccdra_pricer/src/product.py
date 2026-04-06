"""
product.py
==========
CCDRA 產品定義 — 純資料描述，完全不依賴 QuantLib 或市場資料。
依循 QuantLib 設計理念：Instrument 只描述合約條款，
評價邏輯由 PricingEngine 負責。

對應文件「可贖回CMS連結每日區間計息票據」(CCDRA) 的合約條款。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass
class CCDRAProduct:
    """
    可贖回 CMS 連結每日區間計息票據 (CCDRA) 合約條款。

    每日區間計息規則
    ----------------
    每個計息期間，若當日 CMS 指標利率落在 [floor_rate, ceiling_rate] 區間，
    則計入一個「在區間日」。期末按比例支付票面利率 coupon_rate。

    可贖回條款
    ----------
    發行人 (Issuer) 在「封閉期 (freeze_years)」結束後，
    每個付息日可依 call_price 買回票券。
    採用 LSMC（最小二乘蒙地卡羅）評估最優贖回策略。

    技術文件範例參數（p.3, p.21）
    --------------------------------
    effective_date  = 2022/3/31
    maturity_date   = 2027/3/31
    nominal         = 100.0         (USD 10,000,000 → 歸一化為 100)
    coupon_rate     = 1.65%
    floor_rate      = 0.00%
    ceiling_rate    = 4.25%
    call_price      = 100.0
    freeze_years    = 1
    payment_freq    = 4             (Quarterly)
    cms_tenor_years = 2             (2Y CMS, Bloomberg: USSW2)
    credit_spread   = 0.50%
    last_fixing     = 2.55%
    in_days         = 0
    """
    # --- 基本條款 ---
    effective_date: date        # 生效日
    maturity_date: date         # 到期日
    nominal: float              # 名目本金（歸一化，100 = par）

    # --- 票息條款 ---
    coupon_rate: float          # 票面利率（十進位）
    floor_rate: float           # 區間下限（十進位）
    ceiling_rate: float         # 區間上限（十進位）

    # --- 可贖回條款 ---
    call_price: float           # 贖回價格（通常為 100.0 = par）
    freeze_years: int           # 封閉期（年），期間不可贖回

    # --- 付息結構 ---
    payment_freq: int           # 每年付息次數（4 = 季付）
    cms_tenor_years: int        # CMS 指標年期（例如 2 → 2Y CMS）

    # --- 信用與修正 ---
    credit_spread: float        # 信用利差（十進位），用於折現
    last_fixing: float          # 最近一次 CMS 利率觀察值（十進位）
    in_days: int = 0            # 當前計息期已確認在區間的天數

    def __post_init__(self):
        if self.maturity_date <= self.effective_date:
            raise ValueError("到期日必須晚於生效日")
        if not (0 <= self.floor_rate < self.ceiling_rate):
            raise ValueError("floor_rate 必須 < ceiling_rate 且 ≥ 0")
        if self.nominal <= 0:
            raise ValueError("名目本金必須為正數")
        if self.payment_freq not in (1, 2, 4, 12):
            raise ValueError("payment_freq 僅支援 1/2/4/12")

    @property
    def term_years(self) -> float:
        """計算產品總年期（約略值）。"""
        delta = self.maturity_date - self.effective_date
        return delta.days / 365.25

    @property
    def freeze_periods(self) -> int:
        """封閉期對應的付息期數。"""
        return self.freeze_years * self.payment_freq

    @property
    def total_periods(self) -> int:
        """總付息期數（約略值）。"""
        return round(self.term_years * self.payment_freq)

    @classmethod
    def get_example(cls) -> 'CCDRAProduct':
        """
        回傳技術文件範例產品（p.21 參數表）。
        CCDRA Value = 82.0232（含贖回選擇權）。
        """
        return cls(
            effective_date  = date(2022, 3, 31),
            maturity_date   = date(2027, 3, 31),
            nominal         = 100.0,
            coupon_rate     = 0.0165,    # 1.65%
            floor_rate      = 0.0000,    # 0.00%
            ceiling_rate    = 0.0425,    # 4.25%
            call_price      = 100.0,
            freeze_years    = 1,
            payment_freq    = 4,         # Quarterly
            cms_tenor_years = 2,         # 2Y CMS
            credit_spread   = 0.0050,    # 50 bps
            last_fixing     = 0.0255,    # 2.55%
            in_days         = 0,
        )
