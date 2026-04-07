"""
lmm_model.py — LMM 模型建構與市場校準模組（Module 2）

職責：
  1. 組合 LiborForwardModelProcess + ABCD Vol + Corr → LiborForwardModel
  2. 以 Swaption 市場 vol 執行 LevenbergMarquardt 校準
  3. 將已校準的 LiborForwardModel 包裝為實作 RateModelProtocol 的 Adapter，
     讓 Module 3 透過介面使用，不直接依賴 QL 類別

設計原則：
  - ABCDParams / CorrelationParams / CalibrationResult 已移至 types.py。
  - LMMModelAdapter 實作 RateModelProtocol，隔離 QL 依賴於此模組內。
  - LMMCalibrator 輸入 SwaptionQuote 列表，不直接依賴 SwaptionVolSurface 物件。

Dependencies:
    QuantLib-Python (ql)
    types.py, protocols.py
"""

from __future__ import annotations

import time
from typing import Optional

import numpy as np
import QuantLib as ql

from .protocols import RateModelProtocol
from .types import ABCDParams, CalibrationResult, CorrelationParams, SwaptionQuote


# ---------------------------------------------------------------------------
# 模組私有工具函數
# ---------------------------------------------------------------------------


def _parse_tenor(tenor_str: str) -> ql.Period:
    """
    將期限字串轉換為 QuantLib Period。

    Parameters
    ----------
    tenor_str : str
        期限字串，支援 '1Y'、'2YR'、'3M'、'6MO' 等格式（大小寫不拘）。

    Returns
    -------
    ql.Period
        對應的 QuantLib Period 物件。

    Raises
    ------
    ValueError
        若傳入不支援的格式。
    """
    s = tenor_str.strip().upper()
    if s.endswith("YR"):
        return ql.Period(int(s[:-2]), ql.Years)
    elif s.endswith("Y"):
        return ql.Period(int(s[:-1]), ql.Years)
    elif s.endswith("MO"):
        return ql.Period(int(s[:-2]), ql.Months)
    elif s.endswith("M"):
        return ql.Period(int(s[:-1]), ql.Months)
    raise ValueError(
        f"不支援的期限格式：'{tenor_str}'。"
        f"期望格式如 '1Y'、'2YR'、'3M'、'6MO'。"
    )


# ---------------------------------------------------------------------------
# LMMModelBuilder
# ---------------------------------------------------------------------------


class LMMModelBuilder:
    """
    LMM 模型建構器：組合 Process + Vol + Corr，產出 LiborForwardModel。

    Parameters
    ----------
    forward_handle : ql.RelinkableYieldTermStructureHandle
        遠期利率曲線 Handle（來自 Module 1）。
    n_rates : int
        模擬的遠期利率個數。對結構型商品，需比契約到期多一期作折現用。
    libor_tenor_months : int
        LIBOR 基礎指標期數（e.g., 3 代表 Libor 3M）。
    abcd_params : ABCDParams
        ABCD 波動度模型初始參數（校準前）。
    corr_params : CorrelationParams
        相關係數模型初始參數（校準前）。
    """

    def __init__(
        self,
        forward_handle: ql.RelinkableYieldTermStructureHandle,
        n_rates: int,
        libor_tenor_months: int,
        abcd_params: ABCDParams,
        corr_params: CorrelationParams,
    ) -> None:
        self._forward_handle = forward_handle
        self._n_rates = n_rates
        self._libor_tenor_months = libor_tenor_months
        self._abcd = abcd_params
        self._corr = corr_params
        # 儲存子物件，供 LMMModelAdapter 事後取用
        self._process: Optional[ql.LiborForwardModelProcess] = None
        self._vol_model: Optional[ql.LmVolatilityModel] = None
        self._corr_model: Optional[ql.LmCorrelationModel] = None

    def _build_process(self) -> ql.LiborForwardModelProcess:
        """
        建立 LiborForwardModelProcess（USDLibor 3M 為預設指標）。

        Returns
        -------
        ql.LiborForwardModelProcess
            LMM 隨機過程物件。
        """
        index = ql.USDLibor(
            ql.Period(self._libor_tenor_months, ql.Months),
            self._forward_handle,
        )
        self._process = ql.LiborForwardModelProcess(self._n_rates, index)
        return self._process

    def _build_vol_model(
        self, fixing_times: list[float]
    ) -> ql.LmVolatilityModel:
        """
        建立 LmExtLinearExponentialVolModel（ABCD 波動度）。

        Parameters
        ----------
        fixing_times : list[float]
            各遠期利率的 fixing 時間點（年分數），由 process.fixingTimes() 提供。

        Returns
        -------
        ql.LmVolatilityModel
            ABCD 波動度模型物件。
        """
        self._vol_model = ql.LmExtLinearExponentialVolModel(
            fixing_times,
            self._abcd.a,
            self._abcd.b,
            self._abcd.c,
            self._abcd.d,
        )
        return self._vol_model

    def _build_corr_model(self) -> ql.LmCorrelationModel:
        """
        建立 LmLinearExponentialCorrelationModel（指數相關係數）。

        Returns
        -------
        ql.LmCorrelationModel
            相關係數模型物件。
        """
        self._corr_model = ql.LmLinearExponentialCorrelationModel(
            self._n_rates,
            self._corr.rho,
            self._corr.beta,
        )
        return self._corr_model

    def build(self) -> ql.LiborForwardModel:
        """
        組合三個子物件，建立 LiborForwardModel。

        Returns
        -------
        ql.LiborForwardModel
            完整 LMM 模型，可供校準與路徑生成使用。
        """
        process = self._build_process()
        fixing_times = list(process.fixingTimes())
        vol_model = self._build_vol_model(fixing_times)
        corr_model = self._build_corr_model()
        return ql.LiborForwardModel(process, vol_model, corr_model)

    def get_process(self) -> ql.LiborForwardModelProcess:
        """
        回傳 build() 後儲存的 LiborForwardModelProcess。

        用於將 process 傳入 LMMModelAdapter，以取得 fixing times 與
        初始利率等資訊，讓 Adapter 不需直接依賴 LMMModelBuilder。

        Returns
        -------
        ql.LiborForwardModelProcess
            已建立的 LMM 隨機過程物件。

        Raises
        ------
        RuntimeError
            若在呼叫 build() 之前呼叫此方法。
        """
        if self._process is None:
            raise RuntimeError(
                "尚未建立 Process，請先呼叫 build()。"
            )
        return self._process


# ---------------------------------------------------------------------------
# LMMCalibrator
# ---------------------------------------------------------------------------


class LMMCalibrator:
    """
    LMM 市場校準器：以 Swaption 市場 vol 為目標校準模型參數。

    校準流程：
      1. 為每筆 SwaptionQuote 建立 SwaptionHelper
      2. 為每個 Helper 設定 LfmSwaptionEngine
      3. 以 LevenbergMarquardt 執行非線性最佳化
      4. 輸出 CalibrationResult

    Parameters
    ----------
    model : ql.LiborForwardModel
        待校準的 LMM 模型（來自 LMMModelBuilder.build()）。
    forward_handle : ql.RelinkableYieldTermStructureHandle
        遠期利率曲線 Handle（用於 SwaptionHelper 定價）。
    swaption_quotes : list[SwaptionQuote]
        Swaption 波動度報價列表（來自 types.SwaptionQuote）。
    libor_index : Optional[ql.IborIndex]
        LMM 基礎 LIBOR 指標。若傳入 None，預設建立 USDLibor 3M。
    """

    def __init__(
        self,
        model: ql.LiborForwardModel,
        forward_handle: ql.RelinkableYieldTermStructureHandle,
        swaption_quotes: list[SwaptionQuote],
        libor_index: Optional[ql.IborIndex] = None,
    ) -> None:
        self._model = model
        self._forward_handle = forward_handle
        self._swaption_quotes = swaption_quotes
        if libor_index is None:
            libor_index = ql.USDLibor(
                ql.Period(3, ql.Months), forward_handle
            )
        self._libor_index = libor_index
        self._helpers: Optional[list] = None

    def _build_swaption_helpers(self) -> list[ql.CalibrationHelper]:
        """
        為每筆 SwaptionQuote 建立 SwaptionHelper，並設定 LfmSwaptionEngine。

        SwaptionHelper 建構參數：
          - Fixed leg: 年付，30/360 計息基礎
          - Floating leg: Actual/360（USDLibor 慣例）

        Returns
        -------
        list[ql.CalibrationHelper]
            可供 LiborForwardModel.calibrate() 使用的 Helper 列表。
        """
        pricing_engine = ql.LfmSwaptionEngine(
            self._model, self._forward_handle
        )
        helpers = []
        for q in self._swaption_quotes:
            option_period = _parse_tenor(q.option_tenor)
            swap_period = _parse_tenor(q.swap_tenor)
            vol_quote = ql.QuoteHandle(ql.SimpleQuote(q.vol))

            helper = ql.SwaptionHelper(
                option_period,
                swap_period,
                vol_quote,
                self._libor_index,
                ql.Period(1, ql.Years),                # Fixed leg: 年付
                ql.Thirty360(ql.Thirty360.BondBasis),  # Fixed leg day count
                ql.Actual360(),                         # Float leg day count
                self._forward_handle,
            )
            helper.setPricingEngine(pricing_engine)
            helpers.append(helper)

        self._helpers = helpers
        return helpers

    def calibrate(
        self,
        max_iterations: int = 200,
        tolerance: float = 1e-6,
    ) -> CalibrationResult:
        """
        執行 LevenbergMarquardt 非線性最佳化校準。

        Parameters
        ----------
        max_iterations : int
            最大迭代次數，預設 200。
        tolerance : float
            收斂容許誤差，預設 1e-6。

        Returns
        -------
        CalibrationResult
            含校準後參數、RMSE 誤差、收斂狀態與耗時。

        Notes
        -----
        文件範例校準結果：
          a=0.133, b=0.535, c=0.567, d=0.120, rho=0.611, beta=0.599
        """
        helpers = self._build_swaption_helpers()

        optimizer = ql.LevenbergMarquardt(tolerance, tolerance, tolerance)
        end_criteria = ql.EndCriteria(
            max_iterations, 100, tolerance, tolerance, tolerance
        )

        t0 = time.time()
        self._model.calibrate(helpers, optimizer, end_criteria)
        elapsed_ms = (time.time() - t0) * 1000.0

        # 提取校準後參數
        # LiborForwardModel 參數排列：[a, b, c, d, rho, beta]
        params = self._model.params()
        a = float(params[0])
        b = float(params[1])
        c = float(params[2])
        d = float(params[3])
        rho = float(params[4])
        beta = float(params[5])

        # 計算 RMSE（市場 vol 與模型隱含 vol 之差）
        rmse = self._compute_rmse(helpers)

        return CalibrationResult(
            abcd=ABCDParams(a=a, b=b, c=c, d=d),
            correlation=CorrelationParams(rho=rho, beta=beta),
            rmse=rmse,
            elapsed_ms=elapsed_ms,
            is_converged=(rmse < 1e-3),
        )

    def _compute_rmse(
        self, helpers: list[ql.CalibrationHelper]
    ) -> float:
        """
        計算校準誤差的 RMSE（市場 vol vs 模型隱含 vol）。

        Parameters
        ----------
        helpers : list[ql.CalibrationHelper]
            已校準的 SwaptionHelper 列表。

        Returns
        -------
        float
            均方根誤差（RMSE），單位與波動度相同（小數，e.g. 0.01 = 1%）。
        """
        sq_errors = []
        for i, h in enumerate(helpers):
            market_vol = self._swaption_quotes[i].vol
            try:
                model_npv = h.modelValue()
                model_vol = h.impliedVolatility(
                    model_npv,
                    accuracy=1e-6,
                    maxEval=500,
                    minVol=1e-4,
                    maxVol=4.0,
                )
                sq_errors.append((market_vol - model_vol) ** 2)
            except Exception:
                # 若隱含波動度反推失敗（如 NPV 超出範圍），略過此點
                pass

        if not sq_errors:
            return float("nan")
        return float(np.sqrt(np.mean(sq_errors)))

    def calibration_report(self, result: CalibrationResult) -> str:
        """
        產生校準結果的文字比較報告（市場 vol vs 模型 vol）。

        Parameters
        ----------
        result : CalibrationResult
            calibrate() 的回傳值。

        Returns
        -------
        str
            多行文字格式的校準報告。
        """
        sep = "=" * 65
        lines = [
            sep,
            "LMM 校準報告",
            sep,
            f"{'期限':>9}  {'市場 Vol':>10}  {'模型 Vol':>10}  {'誤差':>9}",
            "-" * 65,
        ]

        if self._helpers:
            for i, (q, h) in enumerate(
                zip(self._swaption_quotes, self._helpers)
            ):
                market_vol = q.vol
                try:
                    model_npv = h.modelValue()
                    model_vol = h.impliedVolatility(
                        model_npv,
                        accuracy=1e-6,
                        maxEval=500,
                        minVol=1e-4,
                        maxVol=4.0,
                    )
                    err = market_vol - model_vol
                    lines.append(
                        f"{q.option_tenor:>3}x{q.swap_tenor:<5}"
                        f"  {market_vol:>10.4f}"
                        f"  {model_vol:>10.4f}"
                        f"  {err:>+9.4f}"
                    )
                except Exception:
                    lines.append(
                        f"{q.option_tenor:>3}x{q.swap_tenor:<5}"
                        f"  {market_vol:>10.4f}"
                        f"  {'N/A':>10}"
                        f"  {'N/A':>9}"
                    )

        lines += [
            sep,
            f"ABCD : a={result.abcd.a:.4f}  b={result.abcd.b:.4f}"
            f"  c={result.abcd.c:.4f}  d={result.abcd.d:.4f}",
            f"Corr : rho={result.correlation.rho:.4f}"
            f"  beta={result.correlation.beta:.4f}",
            f"RMSE : {result.rmse:.6f}",
            f"收斂 : {'是' if result.is_converged else '否'}",
            f"耗時 : {result.elapsed_ms:.1f} ms",
            sep,
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# LMMModelAdapter（implements RateModelProtocol）
# ---------------------------------------------------------------------------


class LMMModelAdapter:
    """
    LMM 模型 Protocol Adapter：將 ql.LiborForwardModel 包裝為 RateModelProtocol。

    核心解耦機制：
      MonteCarloEngine 只依賴 RateModelProtocol 介面，不直接持有 QL 物件。
      此 Adapter 將 QL 特定的 API 翻譯為介面方法，隔離 QL 依賴於此類別內。

    未來若替換為 HJM 模型，只需實作另一個 Adapter，MonteCarloEngine 無需修改。

    Parameters
    ----------
    model : ql.LiborForwardModel
        已校準的 LMM 模型物件（來自 LMMCalibrator.calibrate() 後）。
    process : ql.LiborForwardModelProcess
        LMM 隨機過程（含 fixingTimes、initialValues 等資訊）。
        可透過 LMMModelBuilder.get_process() 取得。
    """

    def __init__(
        self,
        model: ql.LiborForwardModel,
        process: ql.LiborForwardModelProcess,
    ) -> None:
        self._model = model
        self._process = process
        # 快取 fixing times，避免每步重複呼叫 QL
        self._fixing_times: list[float] = list(process.fixingTimes())

    def n_rates(self) -> int:
        """
        回傳模擬的遠期利率個數。

        Returns
        -------
        int
            遠期利率個數（來自 process.size()）。
        """
        return self._process.size()

    def initial_rates(self) -> np.ndarray:
        """
        回傳評價日的初始遠期利率向量（來自 process.initialValues()）。

        Returns
        -------
        np.ndarray
            shape: (n_rates,)，初始遠期利率。
        """
        return np.array(list(self._process.initialValues()))

    def compute_covariance(
        self,
        step: int,
        current_rates: np.ndarray,
    ) -> np.ndarray:
        """
        計算共變異數矩陣（GCOV），透過 ABCD Vol 與相關係數矩陣。

        委派至 process.covariance(t, x, dt)，QL 內部依 ABCD vol model
        與 correlation model 組合計算 Σ = A * ρ * A^T * dt。

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
        t0 = self._fixing_times[step]
        dt = self._step_dt(step)
        ql_rates = ql.Array(current_rates.tolist())
        cov_ql = self._process.covariance(t0, ql_rates, dt)

        n = self._process.size()
        cov = np.empty((n, n))
        for i in range(n):
            for j in range(n):
                cov[i, j] = cov_ql[i][j]
        return cov

    def compute_drift(
        self,
        step: int,
        current_rates: np.ndarray,
        covariance: np.ndarray,
    ) -> np.ndarray:
        """
        計算 Spot LIBOR Martingale 測度下的漂移項：
          μ_i = Σ_{j=η(t)}^{i} [L̃_j * δ / (1 + L_j * δ)] * σ_{i,j}

        委派至 process.drift(t, x)，QL 依已校準 vol/corr 模型計算。

        Parameters
        ----------
        step : int
            當前時間步驟索引。
        current_rates : np.ndarray
            shape: (n_rates,)，當前遠期利率向量。
        covariance : np.ndarray
            shape: (n_rates, n_rates)，共變異數矩陣（本方法不使用，由 QL 內部計算）。

        Returns
        -------
        np.ndarray
            shape: (n_rates,)，漂移項向量。
        """
        t0 = self._fixing_times[step]
        ql_rates = ql.Array(current_rates.tolist())
        drift_ql = self._process.drift(t0, ql_rates)
        return np.array(list(drift_ql))

    def evolve(
        self,
        step: int,
        current_rates: np.ndarray,
        brownians: np.ndarray,
    ) -> np.ndarray:
        """
        執行單步對數正態演化，委派至 process.evolve()。

        委派至 QL process.evolve(t0, x, dt, dw)。
        QL 預期 dw 為 N(0, dt) 尺度，此方法內部負責將
        MonteCarloEngine 傳入的 N(0, 1) 標準常態乘以 sqrt(dt) 完成縮放，
        隱藏 QL 特定的時間步驟縮放細節。

        Parameters
        ----------
        step : int
            當前時間步驟索引。
        current_rates : np.ndarray
            shape: (n_rates,)。
        brownians : np.ndarray
            shape: (n_factors,)，標準常態亂數 N(0, 1)（由 MonteCarloEngine 傳入）。

        Returns
        -------
        np.ndarray
            shape: (n_rates,)，下一步遠期利率。
        """
        t0 = self._fixing_times[step]
        dt = self._step_dt(step)
        ql_rates = ql.Array(current_rates.tolist())
        # 將 N(0,1) 縮放為 N(0,dt)，隱藏 QL 的時間步驟縮放需求
        dw_scaled = brownians * np.sqrt(dt)
        ql_dw = ql.Array(dw_scaled.tolist())
        next_ql = self._process.evolve(t0, ql_rates, dt, ql_dw)
        return np.array(list(next_ql))

    def fixing_times(self) -> list[float]:
        """
        回傳各遠期利率的 fixing 時間點列表（供 MonteCarloEngine 使用）。

        Returns
        -------
        list[float]
            長度 = n_rates，各期 fixing 時間（年分數）。
        """
        return self._fixing_times

    def _step_dt(self, step: int) -> float:
        """
        計算第 step 步的時間增量 dt = T_{step+1} - T_{step}。

        若 step 為最後一個合法索引，以前一步的 dt 補充。

        Parameters
        ----------
        step : int
            時間步驟索引。

        Returns
        -------
        float
            時間增量（年分數）。
        """
        times = self._fixing_times
        if step + 1 < len(times):
            return times[step + 1] - times[step]
        elif len(times) >= 2:
            return times[-1] - times[-2]
        return 0.25  # fallback: 3M
