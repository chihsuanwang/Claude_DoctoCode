"""
CCDRA Pricing Library（重構後版本）
=====================================
可贖回 CMS 連結每日區間計息債券（CCDRA）評價套件。

檔案結構（依 QuantLib 解耦原則，共 9 個模組）：

  types.py          — 跨模組資料容器（零 QL 依賴）
  protocols.py      — 抽象介面定義（Protocol）
  instrument.py     — 純資料 dataclass（零業務邏輯）
  schedule.py       — 日期排程計算（依賴 QL Calendar）
  market_data.py    — Module 1：市場資料與利率期間結構
  lmm_model.py      — Module 2：LMM 建構、校準與 Adapter
  rate_calculator.py — CMS 計算 + 區間計息（純 numpy）
  mc_engine.py      — Module 3：MC 路徑演化（依賴 Protocol 介面）
  pricing_engine.py — Module 4：LSMC 定價引擎（純 numpy）

依賴層級（越下層 import 越少）：

  types.py, protocols.py          ← 無內部依賴
      ↓
  instrument.py                   ← 無內部依賴
      ↓
  schedule.py                     ← instrument + QL
  market_data.py                  ← types + protocols + QL
  rate_calculator.py              ← protocols + numpy（無 QL）
      ↓
  lmm_model.py                    ← types + protocols + QL
      ↓
  mc_engine.py                    ← types + protocols + instrument + schedule
      ↓
  pricing_engine.py               ← types（無 QL）

Public API 快速參考：
    from src.types import CCDRASpec  # 錯誤：CCDRASpec 在 instrument.py
    from src.instrument import CCDRASpec
    from src.types import (
        RateQuote, SwaptionQuote,
        ABCDParams, CorrelationParams, CalibrationResult,
        PathSimulationConfig, SimulationResult,
        LSMCConfig, PricingResult,
    )
    from src.protocols import (
        RateModelProtocol, CMSCalculatorProtocol,
        RangeAccrualProtocol, DiscountCurveProtocol,
    )
    from src.schedule import CCDRAScheduleBuilder
    from src.market_data import MarketDataLoader, TermStructureBuilder, SwaptionVolSurface
    from src.lmm_model import LMMModelBuilder, LMMCalibrator, LMMModelAdapter
    from src.rate_calculator import CMSRateCalculator, RangeAccrualInterpolator
    from src.mc_engine import MonteCarloEngine
    from src.pricing_engine import LSMCPricingEngine, BasisFunctionLibrary, BasisType
"""
