"""
market_data.py — 市場資料與利率期間結構模組（Module 1）

職責：
  1. 從 Bloomberg 格式 CSV 讀取市場利率資料，回傳 types.RateQuote 列表
  2. 執行 Bootstrapping，建立 QuantLib PiecewiseFlatForward 曲線
  3. 讀取 Swaption 波動度矩陣，建立 QuantLib SwaptionVolatilityMatrix
  4. 實作 DiscountCurveProtocol，讓下游模組透過介面查詢折現因子

CSV 格式規範（Bloomberg 輸出格式）：
  利率曲線檔（swap_curve.csv）：
    欄位：Term, InstType, Bid, Ask, Mid
    利率單位：百分比（% form），例如 2.5534 代表 2.5534%
    → 程式內部會自動除以 100 轉為小數

  Swaption Vol 檔（swaption_vol.csv）：
    欄位：OptTenor, SwapTenor, Vol
    Vol 單位：小數（decimal form），例如 0.4485 代表 44.85% Black vol
    → 直接使用，不做轉換

Dependencies:
    QuantLib-Python (ql)
    pandas
    types.py, protocols.py
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
import QuantLib as ql

from .protocols import DiscountCurveProtocol
from .types import RateQuote, SwaptionQuote


# ---------------------------------------------------------------------------
# 模組內工具函數
# ---------------------------------------------------------------------------


def _to_ql_date(d: object) -> ql.Date:
    """Python date → ql.Date（若已是 ql.Date 則直接回傳）。"""
    if isinstance(d, ql.Date):
        return d
    from datetime import date as _date
    if isinstance(d, _date):
        return ql.Date(d.day, d.month, d.year)
    raise TypeError(f"Cannot convert {type(d)} to ql.Date")


def _parse_tenor(term: str) -> ql.Period:
    """
    解析期限字串為 ql.Period。

    支援格式（大小寫不拘）：
      月份：'3M', '3MO', '3 MO', '6M', '6MO'
      年份：'2Y', '2YR', '2 YR', '10Y', '10YR'

    Parameters
    ----------
    term : str
        期限字串。

    Returns
    -------
    ql.Period
        對應的 QuantLib Period 物件。

    Raises
    ------
    ValueError
        若字串無法解析。
    """
    t = term.strip().upper().replace(" ", "")
    digits = int("".join(c for c in t if c.isdigit()))

    if t.endswith("MO") or (t.endswith("M") and not t.endswith("YM")):
        return ql.Period(digits, ql.Months)
    elif t.endswith("YR") or t.endswith("Y"):
        return ql.Period(digits, ql.Years)
    else:
        raise ValueError(
            f"無法解析期限字串：'{term}'。"
            f"支援格式：'3M', '3MO', '2Y', '2YR' 等。"
        )


def _tenor_to_years(tenor_str: str) -> float:
    """
    將期限字串轉為年數（浮點），用於排序。

    Parameters
    ----------
    tenor_str : str
        期限字串（e.g., '3M', '2Y'）。

    Returns
    -------
    float
        年數（e.g., '3M' → 0.25，'2Y' → 2.0）。
    """
    t = tenor_str.strip().upper().replace(" ", "")
    digits = int("".join(c for c in t if c.isdigit()))
    if t.endswith("MO") or (t.endswith("M") and not t.endswith("YM")):
        return digits / 12.0
    elif t.endswith("YR") or t.endswith("Y"):
        return float(digits)
    return 0.0


# ---------------------------------------------------------------------------
# Module 1-A：市場資料讀取器
# ---------------------------------------------------------------------------


class MarketDataLoader:
    """
    市場資料讀取器：從 Bloomberg 格式 CSV 載入利率與 Swaption vol 資料。

    Parameters
    ----------
    swap_curve_path : Path
        利率曲線 CSV 路徑。
    swaption_vol_path : Path
        Swaption Vol CSV 路徑。
    """

    def __init__(
        self,
        swap_curve_path: Path,
        swaption_vol_path: Path,
    ) -> None:
        self._swap_curve_path = Path(swap_curve_path)
        self._swaption_vol_path = Path(swaption_vol_path)

    def load_rate_quotes(self) -> list[RateQuote]:
        """
        讀取利率曲線資料，回傳 RateQuote 列表。

        CSV 必要欄位：Term, InstType, Mid（利率單位為 %，程式自動除以 100）。

        Returns
        -------
        list[RateQuote]
            依期限正序排列，含 CASH（短端）與 SWAP（長端）報價。
            RateQuote.mid 以小數表示（e.g., 0.025534 代表 2.5534%）。

        Raises
        ------
        FileNotFoundError
            若 CSV 檔案不存在。
        ValueError
            若 CSV 缺少必要欄位。
        """
        import pandas as pd

        if not self._swap_curve_path.exists():
            raise FileNotFoundError(
                f"利率曲線檔案不存在：{self._swap_curve_path}"
            )

        df = pd.read_csv(self._swap_curve_path)

        # 欄位名稱正規化（去除空白、統一大小寫）
        df.columns = [c.strip() for c in df.columns]
        required = {"Term", "InstType", "Mid"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(
                f"CSV 缺少必要欄位：{missing}。現有欄位：{list(df.columns)}"
            )

        quotes = []
        for _, row in df.iterrows():
            if pd.isna(row["Mid"]):
                continue
            mid_raw = float(row["Mid"])
            # Bloomberg 輸出為 % 形式（e.g., 2.5534），轉為小數
            mid_decimal = mid_raw / 100.0
            quotes.append(
                RateQuote(
                    term=str(row["Term"]).strip(),
                    inst_type=str(row["InstType"]).strip().upper(),
                    mid=mid_decimal,
                )
            )
        return quotes

    def load_swaption_quotes(self) -> list[SwaptionQuote]:
        """
        讀取 Swaption 波動度資料，回傳 SwaptionQuote 列表。

        CSV 必要欄位：OptTenor, SwapTenor, Vol（Vol 為小數形式，e.g., 0.4485 = 44.85%）。

        Returns
        -------
        list[SwaptionQuote]
            所有 (option tenor × swap tenor) 組合的 vol 報價。
            SwaptionQuote.vol 以小數表示（直接使用，不做轉換）。

        Raises
        ------
        FileNotFoundError
            若 CSV 檔案不存在。
        ValueError
            若 CSV 缺少必要欄位。
        """
        import pandas as pd

        if not self._swaption_vol_path.exists():
            raise FileNotFoundError(
                f"Swaption Vol 檔案不存在：{self._swaption_vol_path}"
            )

        df = pd.read_csv(self._swaption_vol_path)
        df.columns = [c.strip() for c in df.columns]
        required = {"OptTenor", "SwapTenor", "Vol"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(
                f"CSV 缺少必要欄位：{missing}。現有欄位：{list(df.columns)}"
            )

        quotes = []
        for _, row in df.iterrows():
            if pd.isna(row["Vol"]) or float(row["Vol"]) <= 0:
                continue
            quotes.append(
                SwaptionQuote(
                    option_tenor=str(row["OptTenor"]).strip(),
                    swap_tenor=str(row["SwapTenor"]).strip(),
                    vol=float(row["Vol"]),
                )
            )
        return quotes


# ---------------------------------------------------------------------------
# Module 1-B：利率期間結構建構器
# ---------------------------------------------------------------------------


class TermStructureBuilder:
    """
    利率期間結構建構器：Bootstrapping → PiecewiseFlatForward → RelinkableHandle。

    同時實作 DiscountCurveProtocol，可直接作為 MonteCarloEngine 的折現來源。

    USD IRS 市場慣例（USSW Bloomberg）：
      - 固定端：Semi-annual, Thirty360 BondBasis
      - 浮動端：Quarterly, 3M LIBOR, Actual360

    Parameters
    ----------
    evaluation_date : date or ql.Date
        評價基準日，同步設定為 QuantLib 全域評價日。
    rate_quotes : list[RateQuote]
        利率報價列表（mid 已為小數形式，來自 MarketDataLoader）。
    calendar : Optional[ql.Calendar]
        交易日曆，預設 ql.UnitedStates(ql.UnitedStates.GovernmentBond)。
    day_count : Optional[ql.DayCounter]
        曲線計算基礎，預設 ql.Actual360()。
    """

    def __init__(
        self,
        evaluation_date: object,
        rate_quotes: list[RateQuote],
        calendar: Optional[ql.Calendar] = None,
        day_count: Optional[ql.DayCounter] = None,
    ) -> None:
        self._eval_date_ql = _to_ql_date(evaluation_date)
        self._rate_quotes = rate_quotes
        self._calendar = calendar or ql.UnitedStates(
            ql.UnitedStates.GovernmentBond
        )
        self._day_count = day_count or ql.Actual360()

        # 設定 QuantLib 全域評價日
        ql.Settings.instance().evaluationDate = self._eval_date_ql

        # 預建立 Handle（後續 build() 時 linkTo 曲線）
        self._discount_handle = ql.RelinkableYieldTermStructureHandle()
        self._forward_handle  = ql.RelinkableYieldTermStructureHandle()
        self._curve: Optional[ql.YieldTermStructure] = None

    def _build_helpers(self) -> list[ql.RateHelper]:
        """
        依 inst_type 建立 RateHelper 列表。

        'CASH' → DepositRateHelper（USD: T+2, ModifiedFollowing, Actual360）
        'SWAP' → SwapRateHelper（USD: Semi-annual fixed 30/360 vs 3M LIBOR）

        Returns
        -------
        list[ql.RateHelper]
            供 PiecewiseFlatForward 使用的 Helper 列表。
        """
        libor_3m = ql.USDLibor(
            ql.Period(3, ql.Months), self._forward_handle
        )

        helpers: list[ql.RateHelper] = []
        for q in self._rate_quotes:
            handle = ql.QuoteHandle(ql.SimpleQuote(q.mid))
            tenor  = _parse_tenor(q.term)

            if q.inst_type == "CASH":
                helper = ql.DepositRateHelper(
                    handle,
                    tenor,
                    2,                      # settlement days
                    self._calendar,
                    ql.ModifiedFollowing,
                    True,                   # end_of_month
                    ql.Actual360(),
                )
            elif q.inst_type == "SWAP":
                helper = ql.SwapRateHelper(
                    handle,
                    tenor,
                    self._calendar,
                    ql.Semiannual,                          # fixed frequency
                    ql.ModifiedFollowing,                   # fixed convention
                    ql.Thirty360(ql.Thirty360.BondBasis),   # fixed day count
                    libor_3m,                               # floating index
                )
            else:
                continue  # 跳過不支援的類型

            helpers.append(helper)

        return helpers

    def build(self) -> tuple[
        ql.RelinkableYieldTermStructureHandle,
        ql.RelinkableYieldTermStructureHandle,
    ]:
        """
        執行 Bootstrapping，產出折現曲線與遠期曲線 Handle。

        採用 PiecewiseFlatForward（分段常數遠期利率），
        啟用外插（enableExtrapolation）確保到期日外仍可查詢。

        Returns
        -------
        tuple[RelinkableHandle, RelinkableHandle]
            (discount_handle, forward_handle)。
            - discount_handle：用於現金流折現（傳入 MonteCarloEngine）
            - forward_handle ：用於 LIBOR 指標計算（傳入 LMMModelBuilder）
        """
        helpers = self._build_helpers()

        self._curve = ql.PiecewiseFlatForward(
            self._eval_date_ql,
            helpers,
            self._day_count,
        )
        self._curve.enableExtrapolation()

        self._discount_handle.linkTo(self._curve)
        self._forward_handle.linkTo(self._curve)

        return self._discount_handle, self._forward_handle

    # --- 實作 DiscountCurveProtocol ---

    def discount_factor(self, target_date: object) -> float:
        """
        查詢特定日期的折現因子 P(0, T)。

        實作 DiscountCurveProtocol，MonteCarloEngine 透過此介面查詢，
        不需直接持有 QuantLib 物件。

        Parameters
        ----------
        target_date : date or ql.Date
            目標日期。

        Returns
        -------
        float
            折現因子 P(0, T)，範圍 (0, 1]。

        Raises
        ------
        RuntimeError
            若 build() 尚未呼叫。
        """
        if self._curve is None:
            raise RuntimeError("請先呼叫 build() 建立利率期間結構。")
        return self._curve.discount(_to_ql_date(target_date))

    def discount_factors(self, target_dates: list) -> np.ndarray:
        """
        批次查詢一組日期的折現因子向量。

        Parameters
        ----------
        target_dates : list[date] or list[ql.Date]
            目標日期列表。

        Returns
        -------
        np.ndarray
            shape: (n_dates,)，折現因子向量。
        """
        return np.array([self.discount_factor(d) for d in target_dates])

    def forward_rate(
        self,
        start_date: object,
        end_date: object,
        compounding: int = ql.Continuous,
    ) -> float:
        """
        查詢兩個日期之間的遠期利率（連續複利）。

        Parameters
        ----------
        start_date : date or ql.Date
            遠期利率起始日。
        end_date : date or ql.Date
            遠期利率終止日。
        compounding : int
            QuantLib Compounding 常數，預設 ql.Continuous。

        Returns
        -------
        float
            遠期利率（以小數表示）。
        """
        if self._curve is None:
            raise RuntimeError("請先呼叫 build() 建立利率期間結構。")
        return self._curve.forwardRate(
            _to_ql_date(start_date),
            _to_ql_date(end_date),
            self._day_count,
            compounding,
        ).rate()


# ---------------------------------------------------------------------------
# Module 1-C：Swaption 波動度曲面
# ---------------------------------------------------------------------------


class SwaptionVolSurface:
    """
    Swaption 波動度曲面：封裝市場 Swaption 隱含波動度矩陣。

    將 SwaptionQuote 列表組織為 QuantLib SwaptionVolatilityMatrix，
    供 LMMCalibrator 的 SwaptionHelper 使用。

    Parameters
    ----------
    evaluation_date : date or ql.Date
        評價基準日。
    swaption_quotes : list[SwaptionQuote]
        Swaption vol 報價列表（vol 已為小數形式）。
    calendar : Optional[ql.Calendar]
        日曆，預設 ql.UnitedStates(ql.UnitedStates.GovernmentBond)。
    day_count : Optional[ql.DayCounter]
        計息基礎，預設 ql.Actual365Fixed()。
    """

    def __init__(
        self,
        evaluation_date: object,
        swaption_quotes: list[SwaptionQuote],
        calendar: Optional[ql.Calendar] = None,
        day_count: Optional[ql.DayCounter] = None,
    ) -> None:
        self._eval_date_ql = _to_ql_date(evaluation_date)
        self._quotes = swaption_quotes
        self._calendar  = calendar  or ql.UnitedStates(ql.UnitedStates.GovernmentBond)
        self._day_count = day_count or ql.Actual365Fixed()

        # 建立查詢用 dict：(option_tenor, swap_tenor) → vol
        self._vol_dict: dict[tuple[str, str], float] = {
            (q.option_tenor, q.swap_tenor): q.vol for q in swaption_quotes
        }

        self._vol_surface: Optional[ql.SwaptionVolatilityStructure] = None
        self._opt_tenors_strs: list[str] = []
        self._swap_tenors_strs: list[str] = []

    def build(self) -> ql.SwaptionVolatilityStructure:
        """
        建構 QuantLib SwaptionVolatilityMatrix。

        步驟：
          1. 從 SwaptionQuote 列表提取唯一 option / swap tenor，依年數排序
          2. 建立 ql.Matrix（列 = option tenor，欄 = swap tenor）
          3. 建立並回傳 SwaptionVolatilityMatrix

        Returns
        -------
        ql.SwaptionVolatilityStructure
            啟用外插的 vol 曲面，可供 SwaptionHelper 直接引用。
        """
        # 依年數排序，確保矩陣順序正確
        self._opt_tenors_strs = sorted(
            set(q.option_tenor for q in self._quotes),
            key=_tenor_to_years,
        )
        self._swap_tenors_strs = sorted(
            set(q.swap_tenor for q in self._quotes),
            key=_tenor_to_years,
        )

        opt_periods  = [_parse_tenor(t) for t in self._opt_tenors_strs]
        swap_periods = [_parse_tenor(t) for t in self._swap_tenors_strs]

        # 填入 vol 矩陣（缺值補 0，後續 Helper 會忽略）
        n_opt  = len(self._opt_tenors_strs)
        n_swap = len(self._swap_tenors_strs)
        vols   = ql.Matrix(n_opt, n_swap, 0.0)

        for i, ot in enumerate(self._opt_tenors_strs):
            for j, st in enumerate(self._swap_tenors_strs):
                vols[i][j] = self._vol_dict.get((ot, st), 0.0)

        self._vol_surface = ql.SwaptionVolatilityMatrix(
            self._eval_date_ql,
            self._calendar,
            ql.Following,
            opt_periods,
            swap_periods,
            vols,
            self._day_count,
        )
        self._vol_surface.enableExtrapolation()
        return self._vol_surface

    def get_vol(self, option_tenor: str, swap_tenor: str) -> float:
        """
        查詢特定 tenor 組合的隱含波動度。

        Parameters
        ----------
        option_tenor : str
            選擇權到期期限（e.g., '1Y'）。
        swap_tenor : str
            底層 Swap 期限（e.g., '5Y'）。

        Returns
        -------
        float
            Black 隱含波動度（小數形式，e.g., 0.4485 = 44.85%）。

        Raises
        ------
        KeyError
            若指定的 tenor 組合不在矩陣中。
        """
        key = (option_tenor, swap_tenor)
        if key not in self._vol_dict:
            raise KeyError(
                f"找不到 Swaption vol ({option_tenor} × {swap_tenor})。"
                f"可用的組合：{list(self._vol_dict.keys())[:5]}..."
            )
        return self._vol_dict[key]

    def option_tenors(self) -> list[str]:
        """
        回傳波動度矩陣的 option tenor 列表（依年數正序）。

        Returns
        -------
        list[str]
            e.g., ['1Y', '2Y', '3Y', '4Y', '5Y']
        """
        return self._opt_tenors_strs.copy()

    def swap_tenors(self) -> list[str]:
        """
        回傳波動度矩陣的 swap tenor 列表（依年數正序）。

        Returns
        -------
        list[str]
            e.g., ['1Y', '2Y', '3Y', '4Y', '5Y', '6Y', '7Y', '8Y', '9Y']
        """
        return self._swap_tenors_strs.copy()
