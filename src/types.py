"""
types.py — 跨模組共用資料容器（零 QuantLib 依賴）

設計原則：
  - 此檔案不 import 任何 QuantLib 或專案內部模組。
  - 所有跨模組傳遞的資料結構集中於此，確保各模組可獨立測試。
  - 使用 dataclass 確保結構清晰，欄位有明確型別宣告。

被以下模組 import：
  market_data.py, lmm_model.py, rate_calculator.py,
  mc_engine.py, pricing_engine.py
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import numpy as np


# ---------------------------------------------------------------------------
# Module 1：市場資料相關型別
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RateQuote:
    """
    單筆利率市場報價。

    Attributes
    ----------
    term : str
        期限標籤（e.g., '3M', '2Y', '10Y'）。
    inst_type : str
        工具種類，'CASH' 代表存款利率，'SWAP' 代表利率交換報價。
    mid : float
        中間價利率，以小數表示（e.g., 0.02554 代表 2.554%）。
    """

    term: str
    inst_type: str
    mid: float


@dataclass(frozen=True)
class SwaptionQuote:
    """
    單筆 Swaption 市場報價。

    Attributes
    ----------
    option_tenor : str
        選擇權到期期限（e.g., '1Y', '2Y'）。
    swap_tenor : str
        底層利率交換期限（e.g., '1Y', '5Y'）。
    vol : float
        Black 隱含波動度，以小數表示（e.g., 0.4485 代表 44.85%）。
    """

    option_tenor: str
    swap_tenor: str
    vol: float


# ---------------------------------------------------------------------------
# Module 2：LMM 模型相關型別
# ---------------------------------------------------------------------------


@dataclass
class ABCDParams:
    """
    LMM ABCD 波動度模型參數。

    波動度函數：σ_i(t) = [a + b*(T_i - t)] * exp(-c*(T_i - t)) + d

    Attributes
    ----------
    a : float
        短端水平移位。初始值 0.5，文件校準結果 ≈ 0.133。
    b : float
        Hump 形狀控制。初始值 0.6，文件校準結果 ≈ 0.535。
    c : float
        指數衰減速率。初始值 0.1，文件校準結果 ≈ 0.567。
    d : float
        長端漸近水準。初始值 0.1，文件校準結果 ≈ 0.120。
    """

    a: float = 0.5
    b: float = 0.6
    c: float = 0.1
    d: float = 0.1


@dataclass
class CorrelationParams:
    """
    LMM 指數相關係數模型參數。

    相關係數：ρ_{i,j} = exp(-beta * |T_i - T_j|) * (1 - rho) + rho

    Attributes
    ----------
    rho : float
        長端相關係數下限，範圍 [0, 1]。文件校準結果 ≈ 0.611。
    beta : float
        相關係數衰減速率。文件校準結果 ≈ 0.599。
    """

    rho: float = 0.5
    beta: float = 0.8


@dataclass
class CalibrationResult:
    """
    LMM 市場校準結果。

    Attributes
    ----------
    abcd : ABCDParams
        校準後的 ABCD 波動度參數。
    correlation : CorrelationParams
        校準後的相關係數參數。
    rmse : float
        市場 vol 與模型 vol 的均方根誤差（Root Mean Square Error）。
    elapsed_ms : float
        校準耗時（毫秒）。
    is_converged : bool
        是否達到收斂條件。
    """

    abcd: ABCDParams
    correlation: CorrelationParams
    rmse: float
    elapsed_ms: float
    is_converged: bool


# ---------------------------------------------------------------------------
# Module 3：Monte Carlo 模擬相關型別
# ---------------------------------------------------------------------------


@dataclass
class PathSimulationConfig:
    """
    Monte Carlo 模擬設定。

    Attributes
    ----------
    n_paths : int
        模擬路徑條數。開發驗證用 1,000；生產用 10,000 以上。
    seed : int
        亂數種子（固定值確保可重現）。
    use_antithetic : bool
        是否啟用對立變量（Antithetic Variates）降低方差。
    use_sobol : bool
        True 使用 Sobol 低差序列；False 使用 Mersenne Twister。
    """

    n_paths: int = 10_000
    seed: int = 42
    use_antithetic: bool = True
    use_sobol: bool = False


@dataclass
class SimulationResult:
    """
    Monte Carlo 模擬輸出，作為 Module 3 → Module 4 的資料傳遞介面。

    Attributes
    ----------
    forward_rate_paths : np.ndarray
        shape: (n_paths, n_steps, n_rates)。
        forward_rate_paths[p, t, i] = 第 p 條路徑第 t 步的第 i 個遠期利率。
    cms_rate_paths : np.ndarray
        shape: (n_paths, n_steps)。
        cms_rate_paths[p, t] = 第 p 條路徑第 t 步的 CMS 利率。
    cashflow_matrix : np.ndarray
        shape: (n_paths, n_periods)。未折現現金流，含最後一期本金返還。
    discount_factors : np.ndarray
        shape: (n_periods,)。各付息日的折現因子 P(0, T_i)。
    accrual_ratios : np.ndarray
        shape: (n_paths, n_periods)。各路徑各期 CMS 落入區間的天數比例。
    period_dates : list[date]
        各計息期付息日列表，長度 = n_periods。
    """

    forward_rate_paths: np.ndarray
    cms_rate_paths: np.ndarray
    cashflow_matrix: np.ndarray
    discount_factors: np.ndarray
    accrual_ratios: np.ndarray
    period_dates: list[date]


# ---------------------------------------------------------------------------
# Module 4：LSMC 定價相關型別
# ---------------------------------------------------------------------------


@dataclass
class LSMCConfig:
    """
    LSMC 演算法設定。

    Attributes
    ----------
    poly_degree : int
        多項式回歸最高次數（文件採用 3，即 [1, x, x², x³]）。
    call_price : float
        贖回價格，以面額百分比表示（e.g., 100.0 = par）。
    freeze_period_steps : int
        凍結期對應的時間步驟數，此期間內禁止行使贖回。
    """

    poly_degree: int = 3
    call_price: float = 100.0
    freeze_period_steps: int = 0


@dataclass
class PricingResult:
    """
    CCDRA 定價輸出結果。

    Attributes
    ----------
    npv : float
        CCDRA 理論價格（以面額百分比表示）。文件範例 ≈ 82.02。
    call_value : float
        贖回選擇權的理論價值。文件範例 ≈ -7.81。
    npv_std_error : float
        NPV 的 Monte Carlo 標準誤差。
    exercise_probability : float
        被提前贖回路徑的比例，範圍 [0, 1]。
    exercise_times : np.ndarray
        shape: (n_paths,)。各路徑最佳行使時點索引（-1 = 持有至到期）。
    path_values_at_zero : np.ndarray
        shape: (n_paths,)。各路徑 t=0 的理論價值，取平均得 NPV。
    """

    npv: float
    call_value: float
    npv_std_error: float
    exercise_probability: float
    exercise_times: np.ndarray
    path_values_at_zero: np.ndarray
