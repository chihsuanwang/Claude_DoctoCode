"""
main.py — CCDRA 定價流程主程式入口（重構後版本）

串接所有模組，依 implementation_plan.md 的 7 步驟完成定價。
每一步驟的輸出僅透過 Protocol 介面或 types.py 資料容器傳遞。
"""

from datetime import date
from pathlib import Path

# 商品定義
from src.instrument import CCDRASpec

# 資料容器（跨模組共用）
from src.types import (
    ABCDParams,
    CorrelationParams,
    LSMCConfig,
    PathSimulationConfig,
)

# 排程計算
from src.schedule import CCDRAScheduleBuilder

# Module 1：市場資料
from src.market_data import MarketDataLoader, SwaptionVolSurface, TermStructureBuilder

# Module 2：LMM 模型
from src.lmm_model import LMMCalibrator, LMMModelAdapter, LMMModelBuilder

# 純數學子模組（無 QL 依賴）
from src.rate_calculator import CMSRateCalculator, RangeAccrualInterpolator

# Module 3：Monte Carlo
from src.mc_engine import MonteCarloEngine

# Module 4：LSMC 定價引擎
from src.pricing_engine import BasisFunctionLibrary, BasisType, LSMCPricingEngine


def build_example_spec() -> CCDRASpec:
    """
    建立文件第五章範例契約的 CCDRASpec。

    Returns
    -------
    CCDRASpec
        評價日 2022/3/31、到期日 2027/3/31 的 CCDRA 契約規格。
    """
    pass


def run_pricing(
    spec: CCDRASpec,
    swap_curve_path: Path,
    swaption_vol_path: Path,
    n_paths: int = 10_000,
) -> None:
    """
    執行完整 CCDRA 定價流程（7 步驟）。

    Parameters
    ----------
    spec : CCDRASpec
        CCDRA 契約規格。
    swap_curve_path : Path
        利率曲線 CSV 路徑。
    swaption_vol_path : Path
        Swaption Vol CSV 路徑。
    n_paths : int
        Monte Carlo 路徑條數。
    """

    # ── Step 1：建立利率期間結構 ────────────────────────────────────────────
    loader = MarketDataLoader(swap_curve_path, swaption_vol_path)
    rate_quotes = loader.load_rate_quotes()

    term_builder = TermStructureBuilder(
        evaluation_date=spec.issue_date,
        rate_quotes=rate_quotes,
    )
    discount_handle, forward_handle = term_builder.build()
    # term_builder 同時實作 DiscountCurveProtocol，直接傳入 MonteCarloEngine

    # ── Step 2：讀取 Swaption Vol 市場資料 ─────────────────────────────────
    swaption_quotes = loader.load_swaption_quotes()
    vol_surface = SwaptionVolSurface(
        evaluation_date=spec.issue_date,
        swaption_quotes=swaption_quotes,
    )

    # ── Step 3：LMM 模型建構與市場校準 ─────────────────────────────────────
    schedule = CCDRAScheduleBuilder(spec).build()

    model_builder = LMMModelBuilder(
        forward_handle=forward_handle,
        n_rates=schedule.n_periods + 1,
        libor_tenor_months=spec.libor_tenor_months,
        abcd_params=ABCDParams(),
        corr_params=CorrelationParams(),
    )
    lmm_model = model_builder.build()

    calibrator = LMMCalibrator(
        model=lmm_model,
        forward_handle=forward_handle,
        swaption_quotes=swaption_quotes,
        libor_index=None,   # 由 LMMCalibrator 內部建立
    )
    calib_result = calibrator.calibrate()
    print(calibrator.calibration_report(calib_result))

    # ── Step 4：建立 LMMModelAdapter（實作 RateModelProtocol）─────────────
    rate_model = LMMModelAdapter(
        model=lmm_model,
        process=None,   # 由 LMMModelBuilder 內部保存，Adapter 從 builder 取得
    )

    # ── Step 5：建立 CMS 計算器與區間計息計算器（均為純 numpy，無 QL）──────
    cms_calculator = CMSRateCalculator(
        cms_tenor_years=spec.cms_tenor_years,
        libor_tenor_months=spec.libor_tenor_months,
        accrual_fraction=0.25,   # Libor 3M = 每期 0.25 年
    )

    range_accrual = RangeAccrualInterpolator(
        floor=spec.floor,
        ceiling=spec.ceiling,
    )

    # ── Step 6：Monte Carlo 路徑模擬 ───────────────────────────────────────
    sim_config = PathSimulationConfig(n_paths=n_paths, seed=42)

    mc_engine = MonteCarloEngine(
        rate_model=rate_model,           # RateModelProtocol
        cms_calculator=cms_calculator,   # CMSCalculatorProtocol
        range_accrual=range_accrual,     # RangeAccrualProtocol
        discount_curve=term_builder,     # DiscountCurveProtocol
        spec=spec,
        schedule=schedule,
        sim_config=sim_config,
    )
    sim_result = mc_engine.simulate()

    # ── Step 7：LSMC 逆向歸納，計算 CCDRA 理論價格 ────────────────────────
    lsmc_config = LSMCConfig(
        poly_degree=3,
        call_price=spec.call_price,
        freeze_period_steps=spec.freeze_years * 4,   # 季付，1年 = 4步
    )

    basis_lib = BasisFunctionLibrary(BasisType.POLYNOMIAL, degree=3)

    pricing_engine = LSMCPricingEngine(
        sim_result=sim_result,
        config=lsmc_config,
        basis_library=basis_lib,
    )
    result = pricing_engine.price()

    # ── 輸出結果 ────────────────────────────────────────────────────────────
    print(f"\n{'='*50}")
    print(f"  CCDRA NPV        : {result.npv:.4f}")
    print(f"  Call Value       : {result.call_value:.4f}")
    print(f"  Std Error        : {result.npv_std_error:.4f}")
    print(f"  Exercise Prob    : {result.exercise_probability:.2%}")
    print(f"{'='*50}")
    print(f"  文件預期 NPV    : 82.0232")
    print(f"  文件預期 Call   : -7.8104")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    spec = build_example_spec()

    run_pricing(
        spec=spec,
        swap_curve_path=Path("data/swap_curve.csv"),
        swaption_vol_path=Path("data/swaption_vol.csv"),
        n_paths=10_000,
    )
