"""
market_data.py
==============
純 Python 資料類別，不依賴 QuantLib。
依循 QuantLib 設計理念：市場資料與模型、產品完全解耦。

CCDRA 技術文件對應：
- SwapCurvePoint  → Swap/Deposit 曲線輸入（Bootstrapping 原料）
- SwaptionVolPoint → Swaption 波動率矩陣（LMM 校準輸入）
- MarketData      → 完整市場資料容器
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional
from datetime import date


@dataclass
class SwapCurvePoint:
    """
    殖利率曲線單一節點。
    對應文件 Swap Curve 欄位：Term / InstType / Rate
    """
    term: str           # 例如 '3M', '2Y', '10Y'
    inst_type: str      # 'CASH'（存款利率）或 'SWAP'（利率交換）
    rate: float         # 年化利率，十進位表示（例如 0.0255 表示 2.55%）

    def __post_init__(self):
        self.inst_type = self.inst_type.upper()
        if self.inst_type not in ('CASH', 'SWAP'):
            raise ValueError(f"inst_type 必須為 'CASH' 或 'SWAP'，得到: {self.inst_type}")
        if not (0 <= self.rate <= 1):
            raise ValueError(f"rate 應為十進位（0~1），得到: {self.rate}")


@dataclass
class SwaptionVolPoint:
    """
    Swaption 波動率矩陣單一節點。
    對應文件 LMM 校準輸入之 Swaption Vol 市場資料。
    使用 Black (Log-normal) 波動率。
    """
    expiry_years: int   # 選擇權到期年數，例如 1, 2, 3
    tenor_years: int    # 利率交換年期，例如 2, 5, 10
    vol: float          # 波動率，十進位（例如 0.30 表示 30%）

    def __post_init__(self):
        if self.expiry_years <= 0 or self.tenor_years <= 0:
            raise ValueError("expiry_years 與 tenor_years 必須為正整數")
        if not (0 < self.vol < 2.0):
            raise ValueError(f"vol 應為合理範圍（0~200%），得到: {self.vol}")


@dataclass
class MarketData:
    """
    完整市場資料容器。
    提供所有 CCDRA 評價所需的外部市場輸入。
    """
    pricing_date: date
    swap_curve: List[SwapCurvePoint] = field(default_factory=list)
    swaption_vols: List[SwaptionVolPoint] = field(default_factory=list)

    def get_swaption_vol(self, expiry: int, tenor: int) -> Optional[float]:
        """查詢特定到期/年期組合的 Swaption 波動率。"""
        for pt in self.swaption_vols:
            if pt.expiry_years == expiry and pt.tenor_years == tenor:
                return pt.vol
        return None

    def add_curve_point(self, term: str, inst_type: str, rate: float) -> None:
        self.swap_curve.append(SwapCurvePoint(term, inst_type, rate))

    def add_swaption_vol(self, expiry: int, tenor: int, vol: float) -> None:
        self.swaption_vols.append(SwaptionVolPoint(expiry, tenor, vol))

    @classmethod
    def get_example_data(cls) -> 'MarketData':
        """
        回傳技術文件第 22 頁之範例市場資料（2022/3/31 評價日）。
        """
        md = cls(pricing_date=date(2022, 3, 31))

        # Swap 曲線（來源：文件第 22 頁 Swap_curve 表格）
        md.add_curve_point('3M',  'CASH', 0.009616)
        md.add_curve_point('2Y',  'SWAP', 0.025534)
        md.add_curve_point('3Y',  'SWAP', 0.026531)
        md.add_curve_point('4Y',  'SWAP', 0.025978)
        md.add_curve_point('5Y',  'SWAP', 0.025229)
        md.add_curve_point('6Y',  'SWAP', 0.024800)
        md.add_curve_point('7Y',  'SWAP', 0.024523)
        md.add_curve_point('8Y',  'SWAP', 0.024300)
        md.add_curve_point('9Y',  'SWAP', 0.024144)
        md.add_curve_point('10Y', 'SWAP', 0.024065)
        md.add_curve_point('12Y', 'SWAP', 0.024040)
        md.add_curve_point('15Y', 'SWAP', 0.023997)
        md.add_curve_point('20Y', 'SWAP', 0.023809)
        md.add_curve_point('25Y', 'SWAP', 0.023185)
        md.add_curve_point('30Y', 'SWAP', 0.022529)

        # Swaption 波動率矩陣（典型 ATM Lognormal Vol，供 LMM 校準用）
        swaption_data = [
            # (expiry, tenor, vol)
            (1, 1, 0.470), (1, 2, 0.430), (1, 3, 0.400), (1, 5, 0.360),
            (2, 1, 0.410), (2, 2, 0.380), (2, 3, 0.360), (2, 5, 0.320),
            (3, 1, 0.370), (3, 2, 0.345), (3, 3, 0.325), (3, 5, 0.295),
            (5, 1, 0.330), (5, 2, 0.310), (5, 3, 0.295), (5, 5, 0.270),
        ]
        for exp, ten, vol in swaption_data:
            md.add_swaption_vol(exp, ten, vol)

        return md
