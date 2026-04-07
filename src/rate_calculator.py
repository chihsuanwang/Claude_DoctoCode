"""
rate_calculator.py — CMS 利率計算與區間計息內插（純數學模組）

職責：
  1. CMSRateCalculator：由 LMM 遠期 LIBOR 路徑推算 CMS swap rate
  2. RangeAccrualInterpolator：計算 CMS 利率落入 [Floor, Ceiling] 的天數比例

設計原則：
  - 此模組不 import QuantLib，只使用 numpy。
  - 兩個 class 分別實作 CMSCalculatorProtocol、RangeAccrualProtocol。
  - 完全可獨立於 QL 環境進行單元測試（純數值輸入輸出）。

Dependencies:
    numpy
    protocols.py, types.py
"""

from __future__ import annotations

import numpy as np

from .protocols import CMSCalculatorProtocol, RangeAccrualProtocol


class CMSRateCalculator:
    """
    CMS 利率計算器：Joshi 改良 Schlogl 內插法。

    由 LMM 模擬出的遠期 LIBOR 路徑，加權計算 x 年期 CMS swap rate：

        SR(t) = (1 - DF[cms_steps]) / Annuity
              = Σ_{j=0}^{cms_steps-1} w_j * L_j

    其中：
        DF[0]   = 1.0
        DF[j+1] = DF[j] / (1 + L_j * δ)
        Annuity = Σ_j DF[j+1] * δ
        w_j     = DF[j+1] * δ / Annuity

    實作 CMSCalculatorProtocol，可替換為其他 CMS 計算方法。

    Parameters
    ----------
    cms_tenor_years : int
        CMS 利率參考年期（e.g., 2 = 2Y CMS）。
    libor_tenor_months : int
        LIBOR 基礎指標期數（e.g., 3 = Libor 3M，δ = 0.25 年）。
    accrual_fraction : float
        每個 LIBOR 期間的年化天數分數（e.g., 3M = 0.25）。

    Examples
    --------
    >>> calc = CMSRateCalculator(cms_tenor_years=2, libor_tenor_months=3, accrual_fraction=0.25)
    >>> fwd_rates = np.array([0.025, 0.026, 0.027, 0.028, 0.029, 0.030, 0.031, 0.032])
    >>> cms = calc.compute_cms_rate(fwd_rates)
    >>> print(f"2Y CMS: {cms:.4%}")
    """

    def __init__(
        self,
        cms_tenor_years: int,
        libor_tenor_months: int,
        accrual_fraction: float,
    ) -> None:
        self._cms_tenor_years = cms_tenor_years
        self._libor_tenor_months = libor_tenor_months
        self._delta = accrual_fraction  # δ = 0.25 for 3M
        # 2Y CMS / 3M LIBOR = 8 steps
        self._cms_steps: int = cms_tenor_years * (12 // libor_tenor_months)

    def _compute_discount_factors(
        self, fwd_rates: np.ndarray
    ) -> np.ndarray:
        """
        由遠期利率向量推算各期折現因子。

        折現因子遞推公式：
          DF[0] = 1.0
          DF[j+1] = DF[j] / (1 + L[j] * δ)

        Parameters
        ----------
        fwd_rates : np.ndarray
            shape: (n_rates,)，遠期利率向量（至少前 cms_steps 個有效）。

        Returns
        -------
        np.ndarray
            shape: (cms_steps + 1,)，折現因子向量（含 DF[0] = 1.0）。
        """
        delta = self._delta
        n = self._cms_steps
        df = np.empty(n + 1)
        df[0] = 1.0
        for j in range(n):
            df[j + 1] = df[j] / (1.0 + fwd_rates[j] * delta)
        return df

    def compute_weights(self, fwd_rates: np.ndarray) -> np.ndarray:
        """
        計算 CMS 利率加權係數 w_j。

        w_j = DF[j+1] * δ / Annuity
        Annuity = Σ_j DF[j+1] * δ  （固定端 Annuity Factor）

        驗證：Σ w_j = 1.0，且 Σ w_j * L_j = CMS rate。

        Parameters
        ----------
        fwd_rates : np.ndarray
            shape: (n_rates,)，當前遠期利率向量。

        Returns
        -------
        np.ndarray
            shape: (cms_steps,)，加權係數向量，Σ w_j = 1.0。
        """
        df = self._compute_discount_factors(fwd_rates)
        df_later = df[1:]  # DF[1] .. DF[cms_steps], shape (cms_steps,)
        annuity = np.sum(df_later) * self._delta
        return (df_later * self._delta) / annuity

    def compute_cms_rate(self, current_fwd_rates: np.ndarray) -> float:
        """
        計算單一時間點的 CMS 利率（實作 CMSCalculatorProtocol）。

        等價公式（兩者數值相同）：
          方法 A：Σ w_j * L_j（加權和）
          方法 B：(1 - DF[cms_steps]) / Annuity（par minus）

        實作採用方法 B，避免 _compute_discount_factors 重複呼叫。

        Parameters
        ----------
        current_fwd_rates : np.ndarray
            shape: (n_rates,)，當前遠期利率向量。

        Returns
        -------
        float
            CMS 利率（以小數表示）。
        """
        df = self._compute_discount_factors(current_fwd_rates)
        df_later = df[1:]  # shape (cms_steps,)
        annuity = float(np.sum(df_later) * self._delta)
        if annuity < 1e-12:
            return 0.0
        return float((1.0 - df[-1]) / annuity)

    def compute_cms_rate_batch(
        self, fwd_rate_matrix: np.ndarray
    ) -> np.ndarray:
        """
        批次計算所有路徑在某一步驟的 CMS 利率（實作 CMSCalculatorProtocol）。

        使用 numpy 完全向量化，避免 Python 層級的 for loop。

        演算法：
          denom[p, j] = 1 + fwd[p, j] * δ
          cumprod[p, j] = prod(denom[p, 0..j])   = 1 / DF[p, j+1]
          df_later[p, j] = 1 / cumprod[p, j]     = DF[p, j+1]
          annuity[p] = Σ_j df_later[p, j] * δ
          cms_rate[p] = (1 - df_later[p, -1]) / annuity[p]

        Parameters
        ----------
        fwd_rate_matrix : np.ndarray
            shape: (n_paths, n_rates)，所有路徑的當前遠期利率矩陣。

        Returns
        -------
        np.ndarray
            shape: (n_paths,)，各路徑的 CMS 利率。
        """
        n = self._cms_steps
        delta = self._delta
        fwd = fwd_rate_matrix[:, :n]  # (n_paths, cms_steps)

        # cumprod[p, j] = prod(1 + fwd[p, 0..j] * delta)
        denom = 1.0 + fwd * delta                           # (n_paths, cms_steps)
        cumprod = np.cumprod(denom, axis=1)                 # (n_paths, cms_steps)
        df_later = 1.0 / cumprod                            # DF[j+1] for j=0..n-1

        annuity = np.sum(df_later, axis=1) * delta          # (n_paths,)
        # Avoid division by zero for degenerate paths
        annuity = np.where(annuity < 1e-12, 1e-12, annuity)

        terminal_df = df_later[:, -1]                       # DF[cms_steps], (n_paths,)
        return (1.0 - terminal_df) / annuity                # (n_paths,)


class RangeAccrualInterpolator:
    """
    區間計息內插法計算器（Interval Interpolation）。

    以相鄰兩時間步驟的 CMS 利率線性估計落入 [Floor, Ceiling] 的天數比例：

        R_H = max(R(T_{i-1}), R(T_i))
        R_L = min(R(T_{i-1}), R(T_i))

        Ratio = 1,                                         若 R_L ≥ B 且 R_H ≤ U
              = 0,                                         若 R_H ≤ B 或 R_L ≥ U
              = [min(U, R_H) - max(B, R_L)] / (R_H - R_L), otherwise

    特殊情況（R_H ≈ R_L）：直接判斷單點是否落入 [B, U]。

    實作 RangeAccrualProtocol，可替換為其他內插方式。

    Parameters
    ----------
    floor : float
        計息區間下限（B），以小數表示（e.g., 0.0 = 0%）。
    ceiling : float
        計息區間上限（U），以小數表示（e.g., 0.0425 = 4.25%）。

    Examples
    --------
    >>> interp = RangeAccrualInterpolator(floor=0.0, ceiling=0.0425)
    >>> ratio = interp.compute_ratio(cms_rate_prev=0.03, cms_rate_curr=0.05)
    >>> print(f"Ratio: {ratio:.4f}")   # 部分落入：(0.0425 - 0.03) / (0.05 - 0.03) = 0.625
    """

    def __init__(self, floor: float, ceiling: float) -> None:
        self._floor = floor
        self._ceiling = ceiling

    def compute_ratio(
        self, cms_rate_prev: float, cms_rate_curr: float
    ) -> float:
        """
        計算單一期間的區間計息天數比例（實作 RangeAccrualProtocol）。

        Parameters
        ----------
        cms_rate_prev : float
            期間起始的 CMS 利率 R(T_{i-1})。
        cms_rate_curr : float
            期間結束的 CMS 利率 R(T_i)。

        Returns
        -------
        float
            落入 [Floor, Ceiling] 的天數比例，範圍 [0.0, 1.0]。
        """
        b, u = self._floor, self._ceiling
        r_h = max(cms_rate_prev, cms_rate_curr)
        r_l = min(cms_rate_prev, cms_rate_curr)

        # 完全超出區間
        if r_h <= b or r_l >= u:
            return 0.0

        # 完全落入區間
        if r_l >= b and r_h <= u:
            return 1.0

        # 退化情況（r_h ≈ r_l）：單點判斷
        denom = r_h - r_l
        if denom < 1e-12:
            mid = (r_h + r_l) * 0.5
            return 1.0 if b <= mid <= u else 0.0

        return (min(u, r_h) - max(b, r_l)) / denom

    def compute_ratio_matrix(
        self, cms_rate_paths: np.ndarray
    ) -> np.ndarray:
        """
        批次計算所有路徑、所有期間的 Ratio 矩陣（實作 RangeAccrualProtocol）。

        使用 numpy 向量化，時間複雜度 O(n_paths * n_periods)，無 Python 迴圈。

        演算法：
          prev  = cms_rate_paths[:, :-1]   # 各期起始 CMS 利率
          curr  = cms_rate_paths[:, 1:]    # 各期結束 CMS 利率
          R_H   = max(prev, curr)
          R_L   = min(prev, curr)
          ratio = clip([min(U,R_H) - max(B,R_L)] / (R_H - R_L), 0, 1)

        退化情況（R_H ≈ R_L）以中點判斷替代除法。

        Parameters
        ----------
        cms_rate_paths : np.ndarray
            shape: (n_paths, n_steps)，CMS 利率路徑矩陣。
            n_steps = n_periods + 1（含 t=0 初始值）。

        Returns
        -------
        np.ndarray
            shape: (n_paths, n_periods)，各路徑各期的 Ratio，值域 [0.0, 1.0]。
        """
        b, u = self._floor, self._ceiling

        # 相鄰對：shape (n_paths, n_periods)
        prev = cms_rate_paths[:, :-1]
        curr = cms_rate_paths[:, 1:]

        r_h = np.maximum(prev, curr)
        r_l = np.minimum(prev, curr)

        # 一般公式（線性內插）
        numerator = np.minimum(u, r_h) - np.maximum(b, r_l)
        denom = r_h - r_l

        degen_mask = denom < 1e-12
        safe_denom = np.where(degen_mask, 1.0, denom)
        ratio_general = np.clip(numerator / safe_denom, 0.0, 1.0)

        # 退化情況：以中點判斷
        mid = (r_h + r_l) * 0.5
        ratio_degen = np.where((mid >= b) & (mid <= u), 1.0, 0.0)

        return np.where(degen_mask, ratio_degen, ratio_general)
