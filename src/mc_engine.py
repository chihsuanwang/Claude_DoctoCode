"""
mc_engine.py — Monte Carlo 路徑生成模組（Module 3）

職責：
  - 透過 RateModelProtocol 驅動遠期利率路徑演化（N paths × M steps）
  - 透過 CMSCalculatorProtocol 計算每步驟的 CMS 利率
  - 透過 RangeAccrualProtocol 計算每期的區間計息比例
  - 依 CCDRASpec 契約條款計算各路徑各期的現金流，產出 SimulationResult

設計原則：
  - 此模組只依賴 Protocol 介面，不直接 import lmm_model.py 或 rate_calculator.py。
  - 替換利率模型、CMS 計算方式或 Ratio 計算方式，此模組完全不需修改。
  - SimulationResult 已移至 types.py，pricing_engine.py 無需 import mc_engine。
  - DiscountCurveProtocol 隔離 QuantLib 折現曲線的直接依賴。

時間步驟慣例：
  - n_steps = n_periods（計息期數）
  - 路徑陣列形狀為 (n_steps + 1, ...)，包含 t=0 初始狀態與 t=1..n_steps 演化狀態
  - cms_rate_paths shape: (n_paths, n_steps + 1)，供 compute_ratio_matrix 使用
  - accrual_ratios shape: (n_paths, n_periods) = (n_paths, n_steps)

Dependencies:
    numpy
    protocols.py, types.py, instrument.py, schedule.py
"""

from __future__ import annotations

import numpy as np

from .instrument import CCDRASpec
from .protocols import (
    CMSCalculatorProtocol,
    DiscountCurveProtocol,
    RangeAccrualProtocol,
    RateModelProtocol,
)
from .schedule import CCDRASchedule
from .types import PathSimulationConfig, SimulationResult


class MonteCarloEngine:
    """
    Monte Carlo 路徑模擬引擎。

    透過四個 Protocol 介面組合功能，完全不依賴具體實作：
      - RateModelProtocol：驅動遠期利率路徑演化
      - CMSCalculatorProtocol：由遠期利率推算 CMS 利率
      - RangeAccrualProtocol：計算 CMS 落入區間的天數比例
      - DiscountCurveProtocol：查詢各付息日的折現因子

    Parameters
    ----------
    rate_model : RateModelProtocol
        利率模型（典型實作：LMMModelAdapter）。
    cms_calculator : CMSCalculatorProtocol
        CMS 利率計算器（典型實作：CMSRateCalculator）。
    range_accrual : RangeAccrualProtocol
        區間計息計算器（典型實作：RangeAccrualInterpolator）。
    discount_curve : DiscountCurveProtocol
        折現曲線（典型實作：TermStructureBuilder）。
    spec : CCDRASpec
        CCDRA 契約規格。
    schedule : CCDRASchedule
        CCDRA 排程（來自 CCDRAScheduleBuilder.build()）。
    sim_config : PathSimulationConfig
        Monte Carlo 模擬設定。

    Examples
    --------
    >>> engine = MonteCarloEngine(
    ...     rate_model=lmm_adapter,        # LMMModelAdapter（實作 RateModelProtocol）
    ...     cms_calculator=cms_calc,       # CMSRateCalculator（實作 CMSCalculatorProtocol）
    ...     range_accrual=range_interp,    # RangeAccrualInterpolator（實作 RangeAccrualProtocol）
    ...     discount_curve=term_builder,   # TermStructureBuilder（實作 DiscountCurveProtocol）
    ...     spec=spec,
    ...     schedule=schedule,
    ...     sim_config=PathSimulationConfig(n_paths=10_000),
    ... )
    >>> result = engine.simulate()
    """

    def __init__(
        self,
        rate_model: RateModelProtocol,
        cms_calculator: CMSCalculatorProtocol,
        range_accrual: RangeAccrualProtocol,
        discount_curve: DiscountCurveProtocol,
        spec: CCDRASpec,
        schedule: CCDRASchedule,
        sim_config: PathSimulationConfig,
    ) -> None:
        self._rate_model = rate_model
        self._cms_calculator = cms_calculator
        self._range_accrual = range_accrual
        self._discount_curve = discount_curve
        self._spec = spec
        self._schedule = schedule
        self._config = sim_config

    def _generate_brownians(
        self, n_steps: int, n_factors: int
    ) -> np.ndarray:
        """
        生成布朗運動增量矩陣（標準常態 N(0,1)）。

        若 use_antithetic=True，先生成 n_paths//2 條路徑，再與其負值串接，
        以對立變量（Antithetic Variates）降低 Monte Carlo 方差。

        Parameters
        ----------
        n_steps : int
            時間步驟數（= n_periods）。
        n_factors : int
            隨機因子數（= LMM 遠期利率個數）。

        Returns
        -------
        np.ndarray
            shape: (n_paths, n_steps, n_factors)，標準常態亂數矩陣 N(0,1)。
        """
        rng = np.random.default_rng(self._config.seed)
        n_paths = self._config.n_paths

        if self._config.use_antithetic:
            half = n_paths // 2
            z = rng.standard_normal((half, n_steps, n_factors))
            return np.concatenate([z, -z], axis=0)

        return rng.standard_normal((n_paths, n_steps, n_factors))

    def _simulate_single_path(
        self,
        brownians: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        模擬單條路徑的遠期利率演化與對應的 CMS 利率。

        時間步驟 t=0 為初始狀態（評價日），t=1..n_periods 為演化後狀態。
        LMMModelAdapter.evolve() 內部已完成 N(0,1) → N(0,dt) 的縮放。

        Parameters
        ----------
        brownians : np.ndarray
            shape: (n_steps, n_factors)，單條路徑的 N(0,1) 布朗運動增量。

        Returns
        -------
        tuple[np.ndarray, np.ndarray]
            - fwd_rate_path: shape (n_periods + 1, n_rates)
              fwd_rate_path[0] = 初始遠期利率（評價日）
            - cms_rate_path:  shape (n_periods + 1,)
              cms_rate_path[0] = 評價日 CMS 利率
        """
        n_periods = self._schedule.n_periods
        n_rates = self._rate_model.n_rates()

        fwd_rate_path = np.empty((n_periods + 1, n_rates))
        cms_rate_path = np.empty(n_periods + 1)

        # t = 0：初始狀態
        current_rates = self._rate_model.initial_rates()
        fwd_rate_path[0] = current_rates
        cms_rate_path[0] = self._cms_calculator.compute_cms_rate(current_rates)

        # t = 1 .. n_periods：逐步演化
        for t in range(n_periods):
            dw = brownians[t]  # N(0,1)，形狀 (n_factors,)
            current_rates = self._rate_model.evolve(t, current_rates, dw)
            fwd_rate_path[t + 1] = current_rates
            cms_rate_path[t + 1] = self._cms_calculator.compute_cms_rate(
                current_rates
            )

        return fwd_rate_path, cms_rate_path

    def _compute_cashflows(
        self,
        cms_rate_path: np.ndarray,
        accrual_ratios: np.ndarray,
    ) -> np.ndarray:
        """
        依區間比例計算單條路徑的各期現金流（未折現）。

        現金流公式（第 i 計息期）：
          CF[i] = Nominal * coupon_rate * Ratio[i] * dcf[i]

        最後一期加回本金（par redemption）：
          CF[n_periods - 1] += Nominal

        Parameters
        ----------
        cms_rate_path : np.ndarray
            shape: (n_periods + 1,)，單條路徑的 CMS 利率序列（此方法未直接使用，
            Ratio 已封裝於 accrual_ratios 中，保留此參數以便未來擴展）。
        accrual_ratios : np.ndarray
            shape: (n_periods,)，各期的區間計息比例。

        Returns
        -------
        np.ndarray
            shape: (n_periods,)，未折現現金流向量（含到期本金）。
        """
        dcf = np.asarray(self._schedule.day_count_fractions)  # (n_periods,)
        nominal = self._spec.nominal
        coupon_rate = self._spec.coupon_rate

        cashflows = nominal * coupon_rate * accrual_ratios * dcf
        cashflows[-1] += nominal  # 到期本金返還
        return cashflows

    def simulate(self) -> SimulationResult:
        """
        執行完整 Monte Carlo 模擬，產出 SimulationResult。

        執行流程：
          1. 生成亂數矩陣 (n_paths, n_periods, n_rates)
          2. 逐路徑演化遠期利率 + 計算 CMS 利率
          3. 批次計算所有路徑的 Ratio 矩陣
          4. 批次計算現金流矩陣
          5. 從 discount_curve 取得折現因子

        Returns
        -------
        SimulationResult
            含遠期利率路徑、CMS 利率路徑、現金流矩陣、
            折現因子向量與區間計息比例矩陣。

        Notes
        -----
        路徑演化（Step 2）使用 Python for loop，為效能瓶頸。
        若需加速，可考慮 numba jit 或將 QL 過程替換為純 numpy 實作。
        建議開發期使用 n_paths=1,000 驗證正確性，
        生產評價使用 n_paths=10,000 以上確保精度。
        """
        n_periods = self._schedule.n_periods
        n_rates = self._rate_model.n_rates()
        n_paths = self._config.n_paths

        # Step 1：生成亂數
        brownians = self._generate_brownians(n_periods, n_rates)
        # shape: (n_paths, n_periods, n_rates)

        # Step 2：路徑演化（含 t=0 初始狀態，故 n_periods + 1 個時間點）
        all_fwd = np.empty((n_paths, n_periods + 1, n_rates))
        all_cms = np.empty((n_paths, n_periods + 1))

        for p in range(n_paths):
            fwd_path, cms_path = self._simulate_single_path(brownians[p])
            all_fwd[p] = fwd_path
            all_cms[p] = cms_path

        # Step 3：批次計算 Ratio 矩陣
        # compute_ratio_matrix: (n_paths, n_periods+1) → (n_paths, n_periods)
        accrual_ratios = self._range_accrual.compute_ratio_matrix(all_cms)

        # Step 4：批次計算現金流（向量化，無 Python loop）
        dcf = np.asarray(self._schedule.day_count_fractions)  # (n_periods,)
        nominal = self._spec.nominal
        coupon_rate = self._spec.coupon_rate

        cashflow_matrix = nominal * coupon_rate * accrual_ratios * dcf
        # shape: (n_paths, n_periods)
        cashflow_matrix[:, -1] += nominal  # 到期本金（各路徑最後一期均加）

        # Step 5：折現因子
        discount_factors = self._discount_curve.discount_factors(
            self._schedule.payment_dates
        )
        # shape: (n_periods,)

        return SimulationResult(
            forward_rate_paths=all_fwd,
            cms_rate_paths=all_cms,
            cashflow_matrix=cashflow_matrix,
            discount_factors=discount_factors,
            accrual_ratios=accrual_ratios,
            period_dates=self._schedule.payment_dates,
        )

    def path_npv_discounted(self, sim_result: SimulationResult) -> np.ndarray:
        """
        計算各路徑的折現 NPV（含本金）。

        NPV[p] = Σ_i cashflow_matrix[p, i] * discount_factors[i]
               = cashflow_matrix @ discount_factors  （矩陣向量乘積）

        Parameters
        ----------
        sim_result : SimulationResult
            simulate() 的回傳值。

        Returns
        -------
        np.ndarray
            shape: (n_paths,)，各路徑的折現 NPV。
        """
        return sim_result.cashflow_matrix @ sim_result.discount_factors
