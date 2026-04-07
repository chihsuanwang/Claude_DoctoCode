"""
protocols.py — 跨模組抽象介面定義（Protocol / ABC）

設計原則：
  - 以 Python typing.Protocol 定義結構性子型別（Structural Subtyping）。
  - 各模組依賴介面而非具體實作，達成「依賴倒置原則（DIP）」。
  - 未來可替換 LMM 為 HJM / SABR 等模型，而不需修改下游模組。

被以下模組 import：
  lmm_model.py, rate_calculator.py, mc_engine.py, pricing_engine.py

Note: Protocol 不需要繼承，只要物件具備對應的方法簽名即可滿足介面。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import numpy as np


# ---------------------------------------------------------------------------
# 利率模型介面
# ---------------------------------------------------------------------------


@runtime_checkable
class RateModelProtocol(Protocol):
    """
    利率模型介面：約束 LMM（或任何替代模型）對外暴露的能力。

    任何實作此介面的物件均可傳入 MonteCarloEngine，
    無需 MonteCarloEngine 知道底層是 LMM、HJM 還是 SABR。
    """

    def n_rates(self) -> int:
        """
        回傳模型模擬的遠期利率個數。

        Returns
        -------
        int
            遠期利率個數（對應 LiborForwardModelProcess.size()）。
        """
        ...

    def initial_rates(self) -> np.ndarray:
        """
        回傳評價日的初始遠期利率向量。

        Returns
        -------
        np.ndarray
            shape: (n_rates,)，各遠期利率初始值（以小數表示）。
        """
        ...

    def compute_covariance(
        self,
        step: int,
        current_rates: np.ndarray,
    ) -> np.ndarray:
        """
        計算特定時間步驟的遠期利率共變異數矩陣（GCOV）。

        Parameters
        ----------
        step : int
            當前時間步驟索引。
        current_rates : np.ndarray
            shape: (n_rates,)，當前遠期利率向量。

        Returns
        -------
        np.ndarray
            shape: (n_rates, n_rates)，共變異數矩陣。
        """
        ...

    def compute_drift(
        self,
        step: int,
        current_rates: np.ndarray,
        covariance: np.ndarray,
    ) -> np.ndarray:
        """
        計算 Spot LIBOR Martingale 測度下的漂移項向量。

        Parameters
        ----------
        step : int
            當前時間步驟索引。
        current_rates : np.ndarray
            shape: (n_rates,)，當前遠期利率向量。
        covariance : np.ndarray
            shape: (n_rates, n_rates)，共變異數矩陣。

        Returns
        -------
        np.ndarray
            shape: (n_rates,)，漂移項向量。
        """
        ...

    def evolve(
        self,
        step: int,
        current_rates: np.ndarray,
        brownians: np.ndarray,
    ) -> np.ndarray:
        """
        執行單一時間步驟的遠期利率對數正態演化。

        Parameters
        ----------
        step : int
            當前時間步驟索引。
        current_rates : np.ndarray
            shape: (n_rates,)，當前遠期利率向量。
        brownians : np.ndarray
            shape: (n_factors,)，本步驟的布朗運動增量。

        Returns
        -------
        np.ndarray
            shape: (n_rates,)，下一步的遠期利率向量。
        """
        ...


# ---------------------------------------------------------------------------
# CMS 利率計算介面
# ---------------------------------------------------------------------------


@runtime_checkable
class CMSCalculatorProtocol(Protocol):
    """
    CMS 利率計算介面：解耦遠期利率路徑與 CMS 利率的推算邏輯。

    MonteCarloEngine 透過此介面呼叫 CMS 計算，
    不知道底層是 Schlogl 法、解析法或其他近似法。
    """

    def compute_cms_rate(self, current_fwd_rates: np.ndarray) -> float:
        """
        由當前遠期利率向量計算單點 CMS 利率。

        Parameters
        ----------
        current_fwd_rates : np.ndarray
            shape: (n_rates,)，當前遠期利率向量。

        Returns
        -------
        float
            CMS 利率（以小數表示）。
        """
        ...

    def compute_cms_rate_batch(
        self, fwd_rate_matrix: np.ndarray
    ) -> np.ndarray:
        """
        批次計算所有路徑在某一步驟的 CMS 利率。

        Parameters
        ----------
        fwd_rate_matrix : np.ndarray
            shape: (n_paths, n_rates)。

        Returns
        -------
        np.ndarray
            shape: (n_paths,)，CMS 利率向量。
        """
        ...


# ---------------------------------------------------------------------------
# 區間計息計算介面
# ---------------------------------------------------------------------------


@runtime_checkable
class RangeAccrualProtocol(Protocol):
    """
    區間計息比例計算介面：解耦 Ratio 計算邏輯與路徑生成。

    MonteCarloEngine 透過此介面呼叫 Ratio 計算，
    底層實作可替換為不同的內插法。
    """

    def compute_ratio(
        self, cms_rate_prev: float, cms_rate_curr: float
    ) -> float:
        """
        計算單一期間 CMS 落入 [Floor, Ceiling] 的天數比例。

        Parameters
        ----------
        cms_rate_prev : float
            期間起始的 CMS 利率。
        cms_rate_curr : float
            期間結束的 CMS 利率。

        Returns
        -------
        float
            落入區間的天數比例，範圍 [0.0, 1.0]。
        """
        ...

    def compute_ratio_matrix(
        self, cms_rate_paths: np.ndarray
    ) -> np.ndarray:
        """
        批次計算所有路徑、所有期間的 Ratio 矩陣。

        Parameters
        ----------
        cms_rate_paths : np.ndarray
            shape: (n_paths, n_steps)。

        Returns
        -------
        np.ndarray
            shape: (n_paths, n_periods)，值域 [0.0, 1.0]。
        """
        ...


# ---------------------------------------------------------------------------
# 折現結構介面
# ---------------------------------------------------------------------------


@runtime_checkable
class DiscountCurveProtocol(Protocol):
    """
    折現曲線介面：解耦 MonteCarloEngine / PricingEngine 對 QuantLib 的直接依賴。

    MonteCarloEngine 與 LSMCPricingEngine 透過此介面查詢折現因子，
    不需直接持有 ql.YieldTermStructure 物件。
    """

    def discount_factor(self, target_date: object) -> float:
        """
        查詢特定日期的折現因子 P(0, T)。

        Parameters
        ----------
        target_date : date or ql.Date
            目標日期。

        Returns
        -------
        float
            折現因子，範圍 (0, 1]。
        """
        ...

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
        ...
