"""
pricing_engine.py — LSMC 可贖回定價引擎（Module 4）

職責：
  1. 接收 SimulationResult（來自 types.py，不 import mc_engine）
  2. 執行 LSMC 逆向歸納（Longstaff-Schwartz backward induction）
  3. 以多項式為回歸基底，估計各時間點的繼續持有條件期望值
  4. 比較繼續持有期望值與贖回 Call Price，確定最佳行使時點
  5. 輸出 PricingResult（CCDRA 理論價格、Call Value、標準誤差等）

設計原則：
  - 此模組完全不 import QuantLib，只使用 numpy。
  - SimulationResult 從 types.py 取用，不依賴 mc_engine.py。
  - 回歸基底函數封裝於 BasisFunctionLibrary，可替換（多項式 / Laguerre）。

Dependencies:
    numpy
    types.py（SimulationResult, PricingResult, LSMCConfig）
"""

from __future__ import annotations

from enum import Enum, auto

import numpy as np

from .types import LSMCConfig, PricingResult, SimulationResult


class BasisType(Enum):
    """
    回歸基底函數類型。

    Attributes
    ----------
    POLYNOMIAL
        單項式基底：[1, x, x², x³]（文件採用方式）。
    LAGUERRE
        Laguerre 正交多項式（Longstaff-Schwartz 原始論文方式）。
    """

    POLYNOMIAL = auto()
    LAGUERRE = auto()


class BasisFunctionLibrary:
    """
    LSMC 回歸基底函數庫：將狀態變數轉換為設計矩陣。

    Parameters
    ----------
    basis_type : BasisType
        基底函數類型，預設 POLYNOMIAL。
    degree : int
        多項式最高次數（POLYNOMIAL 模式），預設 3（含常數項共 4 欄）。

    Examples
    --------
    >>> lib = BasisFunctionLibrary(BasisType.POLYNOMIAL, degree=3)
    >>> x = np.array([0.02, 0.03, 0.04, 0.025])   # 4 條路徑的 CMS 利率
    >>> MX = lib.build_design_matrix(x)
    >>> print(MX.shape)   # (4, 4)：[1, x, x², x³]
    """

    def __init__(
        self,
        basis_type: BasisType = BasisType.POLYNOMIAL,
        degree: int = 3,
    ) -> None:
        pass

    def build_design_matrix(self, state_variable: np.ndarray) -> np.ndarray:
        """
        建立回歸設計矩陣（Design Matrix）。

        POLYNOMIAL（degree=3）輸出格式：
            MX = [[1, x_1, x_1², x_1³],
                  [1, x_2, x_2², x_2³],
                  ...                   ]

        Parameters
        ----------
        state_variable : np.ndarray
            shape: (n_obs,)，狀態變數向量（CMS 利率）。

        Returns
        -------
        np.ndarray
            shape: (n_obs, degree + 1)，回歸設計矩陣。
        """
        pass

    def regress(
        self,
        design_matrix: np.ndarray,
        target_vector: np.ndarray,
    ) -> np.ndarray:
        """
        執行普通最小平方回歸（OLS）。

        MB = (MX^T · MX)^{-1} · MX^T · MY

        Parameters
        ----------
        design_matrix : np.ndarray
            shape: (n_obs, n_basis)，設計矩陣（MX）。
        target_vector : np.ndarray
            shape: (n_obs,)，目標向量（MY）：繼續持有的折現現金流。

        Returns
        -------
        np.ndarray
            shape: (n_basis,)，回歸係數向量（MB）。

        Notes
        -----
        使用 np.linalg.lstsq 取代直接矩陣求逆，
        避免設計矩陣接近奇異時的數值不穩定問題。
        """
        pass

    def predict(
        self,
        coefficients: np.ndarray,
        design_matrix: np.ndarray,
    ) -> np.ndarray:
        """
        以回歸係數計算全部路徑的繼續持有期望值。

        E[CCDRA] ≈ MX · MB

        Parameters
        ----------
        coefficients : np.ndarray
            shape: (n_basis,)，回歸係數。
        design_matrix : np.ndarray
            shape: (n_paths, n_basis)，全部路徑的設計矩陣。

        Returns
        -------
        np.ndarray
            shape: (n_paths,)，繼續持有條件期望值。
        """
        pass


class LSMCPricingEngine:
    """
    最小平方蒙地卡羅法（LSMC）可贖回債券定價引擎。

    實作 Longstaff-Schwartz (2001) 逆向歸納：
      從到期日往評價日方向逐步推進，在每個可贖回時點比較：
        E[繼續持有] vs Call Price
      若提前贖回對發行人有利（E < -1 * (Call + CF)），則更新行使決策。

    Parameters
    ----------
    sim_result : SimulationResult
        Monte Carlo 模擬結果（來自 types.SimulationResult，不綁定 mc_engine）。
    config : LSMCConfig
        LSMC 演算法設定（Call Price、凍結期步驟數、多項式次數）。
    basis_library : BasisFunctionLibrary
        回歸基底函數庫（可替換為不同基底）。

    Examples
    --------
    >>> engine = LSMCPricingEngine(
    ...     sim_result=sim_result,
    ...     config=LSMCConfig(call_price=100.0, freeze_period_steps=4),
    ...     basis_library=BasisFunctionLibrary(BasisType.POLYNOMIAL, degree=3),
    ... )
    >>> result = engine.price()
    >>> print(f"CCDRA NPV: {result.npv:.4f}")   # 預期 ≈ 82.02
    >>> print(f"Call Value: {result.call_value:.4f}")  # 預期 ≈ -7.81
    """

    def __init__(
        self,
        sim_result: SimulationResult,
        config: LSMCConfig,
        basis_library: BasisFunctionLibrary,
    ) -> None:
        pass

    def _initialize_value_matrix(self) -> np.ndarray:
        """
        初始化各路徑各時間步驟的債券持有價值矩陣。

        到期日（最後一步）的價值 = 最後一期現金流（含本金）。
        其餘時間步驟初始化為 0，由逆向歸納逐步填入。

        Returns
        -------
        np.ndarray
            shape: (n_paths, n_steps)，初始化後的價值矩陣。
        """
        pass

    def _is_callable_step(self, step: int) -> bool:
        """
        判斷特定時間步驟是否可行使贖回（凍結期外且為付息日）。

        Parameters
        ----------
        step : int
            時間步驟索引。

        Returns
        -------
        bool
            True = 此步驟可行使贖回；False = 凍結期內或非付息日。
        """
        pass

    def _get_continuation_value(
        self,
        step: int,
        value_matrix: np.ndarray,
    ) -> np.ndarray:
        """
        對所有路徑估計在 step 時點的繼續持有條件期望值。

        步驟：
          1. 取出 step 時點的 CMS 利率（全部路徑）
          2. 建立設計矩陣 MX
          3. 建立目標向量 MY：step+1 至到期日的折現累積現金流
          4. 回歸 MY ~ MX，取得回歸係數
          5. 對全部路徑預測繼續持有期望值

        Parameters
        ----------
        step : int
            當前逆向歸納的時間步驟索引。
        value_matrix : np.ndarray
            shape: (n_paths, n_steps)，當前的價值矩陣。

        Returns
        -------
        np.ndarray
            shape: (n_paths,)，各路徑的繼續持有條件期望值。
        """
        pass

    def _apply_call_decision(
        self,
        step: int,
        continuation_value: np.ndarray,
        value_matrix: np.ndarray,
        exercise_times: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        比較繼續持有期望值與 Call Price，更新行使決策與價值矩陣。

        行使條件（從發行人角度）：
          若 E[繼續持有] < -1 * (Call Price + 當期現金流)
          → 發行人於此時點贖回獲利 → 更新為贖回價值

        Parameters
        ----------
        step : int
            當前時間步驟索引。
        continuation_value : np.ndarray
            shape: (n_paths,)，繼續持有期望值。
        value_matrix : np.ndarray
            shape: (n_paths, n_steps)，當前價值矩陣。
        exercise_times : np.ndarray
            shape: (n_paths,)，各路徑目前記錄的最佳行使時點。

        Returns
        -------
        tuple[np.ndarray, np.ndarray]
            (updated_value_matrix, updated_exercise_times)
        """
        pass

    def price(self) -> PricingResult:
        """
        執行完整 LSMC 逆向歸納，計算 CCDRA 理論價格。

        主迴圈（從 n_steps-2 逆推至 freeze_period_steps+1）：
          for step in range(n_steps - 2, freeze_period_steps, -1):
              if _is_callable_step(step):
                  continuation = _get_continuation_value(step, value_matrix)
                  value_matrix, exercise_times = _apply_call_decision(...)

        最終：
          path_values_at_zero = value_matrix[:, 0]
          npv = mean(path_values_at_zero)
          npv_std_error = std(path_values_at_zero) / sqrt(n_paths)

        Returns
        -------
        PricingResult
            含 NPV、Call Value、標準誤差、行使時點向量與行使機率。

        Notes
        -----
        文件範例預期結果：
          NPV ≈ 82.02（面額 100 為基準）
          Call Value ≈ -7.81
        """
        pass

    def convergence_analysis(
        self,
        path_counts: list[int],
    ) -> dict[int, float]:
        """
        收斂性分析：對不同路徑數計算 NPV，觀察收斂速度。

        Parameters
        ----------
        path_counts : list[int]
            欲測試的路徑數列表（e.g., [1000, 2000, 5000, 10000]）。

        Returns
        -------
        dict[int, float]
            {路徑數: NPV} 的字典，用於繪製收斂曲線。
        """
        pass
