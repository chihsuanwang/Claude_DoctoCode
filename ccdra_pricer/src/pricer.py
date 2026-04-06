"""
pricer.py
=========
CCDRA 評價核心引擎 — 三層解耦架構（QuantLib 設計理念）：

  CurveBuilder    利用 QuantLib 進行利率曲線 Bootstrapping
                  → 提供折現曲線與初始 Forward Rate
  LMMCalibrator   以 Black 公式 + scipy 優化進行 ABCD/相關係數校準
                  → 提供 LMM 模型參數
  CCDRAPricer     numpy LMM 路徑演化 + LSMC 定價
                  → 輸出理論價格與分析數據

依賴方向：CCDRAPricer → LMMCalibrator → CurveBuilder → QuantLib
各層僅依賴下層介面，不共享狀態（Composition over Inheritance）。

對應技術文件：
  CurveBuilder  → p.9  利率期間結構建構（Bootstrapping）
  LMMCalibrator → p.9-11 LMM Calibration（ABCD + 相關係數）
  CCDRAPricer   → p.7-8 LSMC、p.13-16 現金流量計算
"""
from __future__ import annotations

import numpy as np
from scipy.optimize import minimize
from scipy.stats import norm
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass, field
import QuantLib as ql

from .market_data import MarketData
from .product import CCDRAProduct


# ═══════════════════════════════════════════════════════════════
#  工具函式
# ═══════════════════════════════════════════════════════════════

def _to_ql(d) -> ql.Date:
    """Python date → QuantLib Date。"""
    return ql.Date(d.day, d.month, d.year)


def _parse_period(term: str) -> ql.Period:
    """'3M' → ql.Period(3, Months)，'5Y' → ql.Period(5, Years)。"""
    t = term.strip().upper()
    if t.endswith('M'):
        return ql.Period(int(t[:-1]), ql.Months)
    elif t.endswith('Y'):
        return ql.Period(int(t[:-1]), ql.Years)
    raise ValueError(f"無法解析期間字串: {term}")


def _black_swaption_price(fwd_rate: float, strike: float, vol: float,
                          expiry: float, annuity: float) -> float:
    """ATM Black (Lognormal) Swaption 公式，用於 LMM 校準目標函數。"""
    if vol <= 0 or expiry <= 0 or annuity <= 0:
        return 0.0
    d1 = (np.log(fwd_rate / strike) + 0.5 * vol**2 * expiry) / (vol * np.sqrt(expiry))
    d2 = d1 - vol * np.sqrt(expiry)
    return annuity * (fwd_rate * norm.cdf(d1) - strike * norm.cdf(d2))


# ═══════════════════════════════════════════════════════════════
#  Layer 1：利率曲線建構器（QuantLib）
# ═══════════════════════════════════════════════════════════════

class CurveBuilder:
    """
    利率曲線 Bootstrapping（對應文件 p.9）。
    使用 QuantLib DepositRateHelper + SwapRateHelper
    建構 PiecewiseLogLinearDiscount 折現曲線。
    """
    CALENDAR    = ql.UnitedStates(ql.UnitedStates.GovernmentBond)
    DEPOSIT_DC  = ql.Actual360()
    FIXED_DC    = ql.Thirty360(ql.Thirty360.BondBasis)
    SETTLE_DAYS = 2

    def __init__(self, market_data: MarketData):
        self.market_data = market_data
        self.curve: Optional[ql.YieldTermStructure] = None
        self.handle: Optional[ql.RelinkableYieldTermStructureHandle] = None

    def build(self) -> Tuple[ql.YieldTermStructure,
                             ql.RelinkableYieldTermStructureHandle]:
        """
        建構折現曲線，回傳 (curve, handle)。
        handle 採用 RelinkableYieldTermStructureHandle：
        外部元件持有 handle，可在重新連結時自動更新（QuantLib Observer 模式）。
        """
        pricing_date = _to_ql(self.market_data.pricing_date)
        ql.Settings.instance().evaluationDate = pricing_date

        helpers = self._build_helpers()
        self.curve = ql.PiecewiseLogLinearDiscount(
            pricing_date, helpers, self.DEPOSIT_DC
        )
        self.curve.enableExtrapolation()

        self.handle = ql.RelinkableYieldTermStructureHandle()
        self.handle.linkTo(self.curve)
        return self.curve, self.handle

    def _build_helpers(self) -> list:
        helpers = []
        for pt in self.market_data.swap_curve:
            q      = ql.QuoteHandle(ql.SimpleQuote(pt.rate))
            period = _parse_period(pt.term)
            if pt.inst_type == 'CASH':
                h = ql.DepositRateHelper(
                    q, period, self.SETTLE_DAYS,
                    self.CALENDAR, ql.ModifiedFollowing,
                    True, self.DEPOSIT_DC
                )
            else:  # SWAP
                h = ql.SwapRateHelper(
                    q, period, self.CALENDAR,
                    ql.Annual, ql.ModifiedFollowing,
                    self.FIXED_DC,
                    ql.USDLibor(ql.Period(3, ql.Months))
                )
            helpers.append(h)
        return helpers

    def get_initial_libor_rates(self, n_rates: int,
                                 delta: float = 0.25) -> np.ndarray:
        """
        從折現曲線提取初始 3M LIBOR Forward Rate（離散化）。
        F_i = [P(0,T_i)/P(0,T_{i+1}) - 1] / delta
        """
        if self.curve is None:
            raise RuntimeError("請先呼叫 build()")
        pricing_ql = _to_ql(self.market_data.pricing_date)
        rates = []
        for i in range(n_rates):
            t_start = i * delta
            t_end   = (i + 1) * delta
            try:
                P_start = self.curve.discount(
                    _offset_date(pricing_ql, t_start))
                P_end   = self.curve.discount(
                    _offset_date(pricing_ql, t_end))
                F = (P_start / P_end - 1.0) / delta
            except Exception:
                F = 0.02  # 後備值
            rates.append(max(F, 1e-4))
        return np.array(rates)

    def get_fixing_times(self, n_rates: int, delta: float = 0.25) -> np.ndarray:
        """Forward rate 的 fixing time（期末時間）。"""
        return np.array([(i + 1) * delta for i in range(n_rates)])


def _offset_date(base: ql.Date, years: float) -> ql.Date:
    """將 QuantLib Date 向後位移 years 年（使用實際天數近似）。"""
    days = int(years * 365.25)
    return base + days


# ═══════════════════════════════════════════════════════════════
#  Layer 2：LMM 模型校準器（pure numpy/scipy + Black 公式）
# ═══════════════════════════════════════════════════════════════

class LMMCalibrator:
    """
    LMM ABCD 波動率 + 指數衰減相關係數校準（對應文件 p.9-11）。

    校準目標：使模型 Swaption 價格（Black 公式 × ABCD 積分波動率）
              貼近市場 Swaption 波動率。

    波動率函數（ABCD Model，文件公式）：
      σ_i(τ) = (a·τ + b)·exp(-c·τ) + d,  τ = T_i - t

    相關係數（指數衰減）：
      ρ_{ij} = ρ∞ + (1−ρ∞)·exp(−β·|i−j|)
    """

    DELTA = 0.25  # 3M 計息因子

    def __init__(self,
                 market_data: MarketData,
                 curve: ql.YieldTermStructure,
                 cms_tenor_years: int = 2,
                 maturity_years: float = 5.0,
                 a: float = 0.30, b: float = 0.10,
                 c: float = 0.50, d: float = 0.10,
                 rho: float = 0.50, beta: float = 0.10):
        self.market_data     = market_data
        self.curve           = curve
        self.cms_tenor_years = cms_tenor_years
        self.maturity_years  = maturity_years

        # 所需 3M Forward Rate 數量
        total_y    = maturity_years + cms_tenor_years + 1.0
        self.size  = max(int(np.ceil(total_y * 4)), 8)

        self.params = np.array([a, b, c, d, rho, beta], dtype=float)
        self.fixing_times  = np.arange(1, self.size + 1) * self.DELTA
        self.initial_rates: Optional[np.ndarray] = None  # 由 CurveBuilder 填入

    # ── 公開介面 ──────────────────────────────

    def calibrate(self, use_market_vols: bool = True) -> Dict:
        """
        執行 Swaption 市場校準（若 use_market_vols=False 則保留初始參數）。
        回傳校準後參數 {a, b, c, d, rho, beta}。
        """
        if not use_market_vols or not self.market_data.swaption_vols:
            return self._params_dict()

        # 建立 (expiry, tenor, market_vol, fwd_swap_rate, annuity) 清單
        targets = self._build_calibration_targets()
        if not targets:
            return self._params_dict()

        def objective(x):
            a, b, c, d, rho, beta = np.clip(
                x, [0.001, -0.5, 0.001, 0.001, 0.0, 0.001],
                   [2.0,   1.0,  5.0,  1.0,  0.99, 1.0])
            total = 0.0
            for (exp, ten, mkt_vol, fwd, ann) in targets:
                model_vol = self._integrated_vol(a, b, c, d, exp,
                                                 self.size, ten)
                mkt_prc = _black_swaption_price(fwd, fwd, mkt_vol, exp, ann)
                mdl_prc = _black_swaption_price(fwd, fwd, model_vol, exp, ann)
                total += (mkt_prc - mdl_prc) ** 2
            return total

        res = minimize(objective, self.params,
                       method='Nelder-Mead',
                       options={'maxiter': 3000, 'xatol': 1e-6, 'fatol': 1e-8})
        if res.success or res.fun < 1e-4:
            self.params = np.clip(
                res.x,
                [0.001, -0.5, 0.001, 0.001, 0.0, 0.001],
                [2.0,   1.0,  5.0,  1.0,  0.99, 1.0])

        return self._params_dict()

    # ── 私有：校準輔助 ────────────────────────

    def _build_calibration_targets(self) -> list:
        """建立校準目標清單：(expiry, tenor, mkt_vol, fwd_swap, annuity)。"""
        targets = []
        pricing_ql = _to_ql(self.market_data.pricing_date)
        for vpt in self.market_data.swaption_vols:
            exp = vpt.expiry_years
            ten = vpt.tenor_years
            mkt_vol = vpt.vol
            # 計算 Forward Swap Rate 與 Annuity
            try:
                fwd, ann = self._calc_fwd_swap(pricing_ql, exp, ten)
                targets.append((exp, ten, mkt_vol, fwd, ann))
            except Exception:
                continue
        return targets

    def _calc_fwd_swap(self, pricing_ql: ql.Date,
                       expiry_y: int, tenor_y: int) -> Tuple[float, float]:
        """計算 Forward Swap Rate 與 Annuity（由 Discount Curve）。"""
        delta    = self.DELTA
        n_fixed  = tenor_y  # 年付次數 × 年期 (簡化為年付)
        n_float  = tenor_y * 4

        # Annuity（固定腿，年付）
        ann = 0.0
        for k in range(1, n_fixed + 1):
            t = expiry_y + k
            ann += self.curve.discount(_offset_date(pricing_ql, t))

        # Forward Swap Rate
        P_start = self.curve.discount(_offset_date(pricing_ql, expiry_y))
        P_end   = self.curve.discount(_offset_date(pricing_ql, expiry_y + tenor_y))
        fwd_swap = (P_start - P_end) / ann if ann > 1e-10 else 0.025
        return float(fwd_swap), float(ann)

    @staticmethod
    def _integrated_vol(a: float, b: float, c: float, d: float,
                        expiry: float, n_rates: int, tenor: int) -> float:
        """
        ABCD 積分波動率（用於 Swaption 定價）：
        Σ_vol² = (1/T) ∫₀ᵀ Σᵢ Σⱼ ρᵢⱼ σᵢ(s) σⱼ(s) wᵢ wⱼ ds

        簡化：取對應 tenor 各 forward rate 的加權平均積分波動率。
        """
        delta = 0.25
        # 對應 expiry 開始的 forward rates（前 tenor*4 個）
        n_cms = tenor * 4
        tau_vals = np.array([(k + 1) * delta + expiry for k in range(n_cms)])

        # 數值積分（n_steps 步梯形法）
        n_steps = 50
        t_grid  = np.linspace(0, expiry, n_steps + 1)
        dt      = expiry / n_steps if expiry > 0 else 1.0
        var_sum = 0.0
        for t in t_grid[:-1]:
            tau = tau_vals - t  # 每個 forward rate 的剩餘時間
            tau = np.maximum(tau, 0)
            sig = (a * tau + b) * np.exp(-c * tau) + d
            sig = np.maximum(sig, 1e-4)
            var_sum += np.mean(sig)**2 * dt

        return float(np.sqrt(var_sum / expiry)) if expiry > 0 else d

    def _params_dict(self) -> Dict:
        return {
            'a': float(self.params[0]), 'b': float(self.params[1]),
            'c': float(self.params[2]), 'd': float(self.params[3]),
            'rho': float(self.params[4]), 'beta': float(self.params[5]),
        }


# ═══════════════════════════════════════════════════════════════
#  評價結果容器（Value Object）
# ═══════════════════════════════════════════════════════════════

@dataclass
class PricingResult:
    """CCDRA 評價結果 — 純資料容器，無行為（Value Object）。"""
    price:              float = 0.0
    price_no_call:      float = 0.0
    call_option_value:  float = 0.0
    call_probability:   float = 0.0
    calibrated_params:  Dict  = field(default_factory=dict)
    coupon_schedule:    List[Dict] = field(default_factory=list)
    cms_paths_sample:   Optional[np.ndarray] = None
    error:              Optional[str] = None
    n_paths:            int   = 0
    runtime_sec:        float = 0.0


# ═══════════════════════════════════════════════════════════════
#  Layer 3：CCDRA 定價引擎（numpy LMM MC + LSMC）
# ═══════════════════════════════════════════════════════════════

class CCDRAPricer:
    """
    CCDRA 完整評價引擎。

    實施步驟（對應文件 p.8）：
    (1) LMM 演化方程式產生 3M LIBOR Forward Rate 路徑
    (2) 由 forward rate 路徑計算 CMS 利率路徑
    (3) 計算每期票息（每日在區間比例 × 票面利率）
    (4) LSMC 估計各時點繼續持有現值
    (5) 選取最大值（繼續持有 vs. 被贖回）
    (6) 回溯至 t=0 得到理論價格
    (7) 所有路徑平均 → CCDRA 理論價格
    """

    def __init__(self,
                 product:          CCDRAProduct,
                 market_data:      MarketData,
                 n_paths:          int   = 5000,
                 n_steps_per_year: int   = 12,
                 seed:             int   = 42,
                 a:    float = 0.30, b:    float = 0.10,
                 c:    float = 0.50, d:    float = 0.10,
                 rho:  float = 0.50, beta: float = 0.10,
                 calibrate: bool = True):

        self.product          = product
        self.market_data      = market_data
        self.n_paths          = n_paths
        self.n_steps_per_year = n_steps_per_year
        self.seed             = seed
        self.lmm_params       = dict(a=a, b=b, c=c, d=d, rho=rho, beta=beta)
        self.do_calibrate     = calibrate

        # 內部快取（計算後填入）
        self._coupon_dates: Optional[List[ql.Date]] = None
        self._sim_times:    Optional[np.ndarray]    = None

    # ── 公開介面 ──────────────────────────────

    def price(self, progress_callback=None) -> PricingResult:
        """執行完整評價，回傳 PricingResult。"""
        import time
        t0 = time.time()
        result = PricingResult(n_paths=self.n_paths)

        def _prog(pct, msg):
            if progress_callback:
                progress_callback(pct, msg)

        try:
            # ── Step 1：建構折現曲線 ──
            _prog(5, "建構利率曲線（Bootstrapping）…")
            builder = CurveBuilder(self.market_data)
            curve, handle = builder.build()

            # ── Step 2：建構 LMM 校準器 ──
            _prog(10, "建構 LMM 模型結構…")
            cal = LMMCalibrator(
                self.market_data, curve,
                cms_tenor_years=self.product.cms_tenor_years,
                maturity_years=self.product.term_years,
                **self.lmm_params
            )
            cal.initial_rates = builder.get_initial_libor_rates(cal.size)

            # ── Step 3：LMM 參數校準 ──
            _prog(20, "LMM Swaption 校準中（ABCD + 相關係數）…")
            cal_params = cal.calibrate(use_market_vols=self.do_calibrate)
            result.calibrated_params = cal_params

            # ── Step 4：蒙地卡羅路徑模擬 ──
            _prog(35, f"蒙地卡羅模擬 {self.n_paths:,} 條路徑…")
            np.random.seed(self.seed)
            fwd_paths = self._simulate_libor_paths(cal)

            # ── Step 5：計算 CMS 利率路徑 ──
            _prog(60, "由 Forward Rate 計算 CMS 利率路徑…")
            cms_paths = self._compute_cms_paths(fwd_paths, cal)
            result.cms_paths_sample = cms_paths[:min(100, self.n_paths)]

            # ── Step 6：建構付息時程 ──
            _prog(70, "建構季付付息時程…")
            self._coupon_dates = self._build_coupon_dates()

            # ── Step 7：計算現金流量 ──
            _prog(76, "計算每期票息現金流量（區間比例法）…")
            coupon_flows, disc_factors = self._calc_coupon_flows(
                cms_paths, curve
            )

            # ── Step 8：LSMC ──
            _prog(85, "LSMC 最小二乘蒙地卡羅（可贖回選擇權定價）…")
            price_call, call_prob = self._lsmc(
                coupon_flows, cms_paths, disc_factors
            )

            # ── Step 9：不含贖回價格 ──
            _prog(94, "計算不含贖回理論價格（純 CDRA）…")
            price_nc = self._price_no_call(coupon_flows, disc_factors)

            result.price             = price_call
            result.price_no_call     = price_nc
            result.call_option_value = price_nc - price_call
            result.call_probability  = call_prob
            result.coupon_schedule   = self._build_schedule_summary(
                cms_paths, coupon_flows, disc_factors
            )
            result.runtime_sec = time.time() - t0
            _prog(100, "評價完成！")

        except Exception:
            import traceback
            result.error = traceback.format_exc()

        return result

    # ── Layer 3a：LMM 蒙地卡羅路徑演化 ──────────

    def _simulate_libor_paths(self, cal: LMMCalibrator) -> np.ndarray:
        """
        LMM Forward Rate 路徑演化（對數常態 Euler-Maruyama）。

        技術文件公式（p.12）：
          log F_i(t+Δt) = log F_i(t)
                        + [μ_i(t) − ½σ_i²(t)] Δt
                        + σ_i(t) √Δt Z_i

        漂移項（Spot LIBOR Measure）：
          μ_i(t) = Σ_{j=m(t)}^{i} ρ_{ij} σ_i(t) σ_j(t) δ F_j(t) / (1+δF_j(t))
        """
        size     = cal.size
        F0       = cal.initial_rates.copy()
        ftimes   = cal.fixing_times    # shape (size,)
        a, b, c, d   = (cal.params[k] for k in range(4))
        rho, beta    = cal.params[4], cal.params[5]
        delta    = cal.DELTA

        # 時間網格
        T       = self.product.term_years
        n_steps = max(int(T * self.n_steps_per_year), 24)
        times   = np.linspace(0.0, T, n_steps + 1)
        dt      = times[1] - times[0]
        sqrt_dt = np.sqrt(dt)
        self._sim_times = times

        # 相關矩陣與 Cholesky 分解
        corr = _build_corr_matrix(size, rho, beta)
        L    = _safe_cholesky(corr)

        # 路徑矩陣 shape = (n_paths, size, n_steps+1)
        paths = np.zeros((self.n_paths, size, n_steps + 1))
        paths[:, :, 0] = np.maximum(F0, 1e-5)

        for step in range(n_steps):
            t  = times[step]
            F  = paths[:, :, step]           # (n_paths, size)

            # 瞬時波動率向量（ABCD）
            tau  = np.maximum(ftimes - t, 0.0)
            vols = (a * tau + b) * np.exp(-c * tau) + d
            vols = np.maximum(vols, 5e-4)    # shape (size,)

            # 當前 alive 最小 index
            m = int(np.searchsorted(ftimes, t, side='right'))

            # LMM 漂移（向量化）
            # drift_i = Σ_{j=m}^{i} ρ_{ij} σ_i σ_j δ F_j/(1+δF_j)
            drift = np.zeros((self.n_paths, size))
            for i in range(m, size):
                acc = np.zeros(self.n_paths)
                for j in range(m, i + 1):
                    acc += (corr[i, j] * vols[i] * vols[j]
                            * delta * F[:, j] / (1.0 + delta * F[:, j]))
                drift[:, i] = acc

            # 相關標準正態隨機數
            Z_raw = np.random.standard_normal((self.n_paths, size))
            Z     = Z_raw @ L.T              # (n_paths, size)

            # 對數常態更新
            alive    = (ftimes > t)          # (size,) mask
            log_F    = np.log(np.maximum(F, 1e-10))
            log_F_new = (log_F
                         + (drift - 0.5 * vols**2) * dt
                         + vols * sqrt_dt * Z)
            F_new = np.exp(log_F_new)
            F_new[:, ~alive] = F[:, ~alive]  # 已過期的 rate 不再更新
            paths[:, :, step + 1] = np.maximum(F_new, 1e-5)

        return paths

    # ── Layer 3b：CMS 利率計算 ───────────────────

    def _compute_cms_paths(self, fwd_paths: np.ndarray,
                           cal: LMMCalibrator) -> np.ndarray:
        """
        由 Forward LIBOR 路徑推算 CMS Swap Rate 路徑。

        技術文件公式（p.14）：
          S(t, n) = [P(t,T_start) − P(t,T_end)] / A(t)
          A(t)    = δ Σ_{k=1}^{n_CMS} P(t, T_{m+k})
          P(t,T_k)= Π_{j=m}^{k-1} 1/(1 + δ F_j(t))
        """
        n_paths, size, n_times = fwd_paths.shape
        cms_periods = self.product.cms_tenor_years * 4
        delta       = cal.DELTA
        ftimes      = cal.fixing_times

        cms_paths = np.zeros((n_paths, n_times))

        for t_idx in range(n_times):
            t  = self._sim_times[t_idx]
            F  = fwd_paths[:, :, t_idx]                     # (n_paths, size)
            m  = int(np.searchsorted(ftimes, t, side='right'))

            if m + cms_periods >= size:
                idx = min(m, size - 1)
                cms_paths[:, t_idx] = F[:, idx]
                continue

            # 逐步計算 Discount Factor（Ratio 法）
            # P[k] = P(t, T_{m+k}) / P(t, T_m)，P[0] = 1
            P = np.ones((n_paths, cms_periods + 1))
            for k in range(1, cms_periods + 1):
                j = m + k - 1
                if j < size:
                    P[:, k] = P[:, k-1] / (1.0 + delta * F[:, j])
                else:
                    P[:, k] = P[:, k-1]

            A   = delta * np.sum(P[:, 1:], axis=1)           # Annuity
            valid = A > 1e-10
            cms = np.where(valid,
                           (P[:, 0] - P[:, cms_periods]) / A,
                           F[:, min(m, size-1)])
            cms_paths[:, t_idx] = np.clip(cms, -0.02, 0.30)

        return cms_paths

    # ── Layer 3c：付息時程 ───────────────────────

    def _build_coupon_dates(self) -> List[ql.Date]:
        """QuantLib Schedule 建構季付付息時程。"""
        eff  = _to_ql(self.product.effective_date)
        mat  = _to_ql(self.product.maturity_date)
        cal  = ql.UnitedStates(ql.UnitedStates.GovernmentBond)
        freq_map = {1: ql.Annual, 2: ql.Semiannual,
                    4: ql.Quarterly, 12: ql.Monthly}
        freq = freq_map.get(self.product.payment_freq, ql.Quarterly)

        sched = ql.Schedule(
            eff, mat, ql.Period(freq), cal,
            ql.ModifiedFollowing, ql.ModifiedFollowing,
            ql.DateGeneration.Forward, False
        )
        return [sched[i] for i in range(len(sched))]

    # ── Layer 3d：現金流量計算 ───────────────────

    def _calc_coupon_flows(self,
                           cms_paths: np.ndarray,
                           curve: ql.YieldTermStructure
                           ) -> Tuple[np.ndarray, np.ndarray]:
        """
        計算每期票息現金流量。

        技術文件公式（p.7）：
          C_i = F_i/N_i × Σ_{days in period} coupon × 1{floor ≤ CMS ≤ ceiling}

        本實作以模擬時間步驟之在區間比例近似每日比例。
        """
        prod          = self.product
        pricing_ql    = _to_ql(self.market_data.pricing_date)
        coupon_dates  = self._coupon_dates
        n_periods     = len(coupon_dates) - 1
        n_paths       = cms_paths.shape[0]
        n_times       = cms_paths.shape[1]
        dc_accrual    = ql.Thirty360(ql.Thirty360.BondBasis)
        dc_time       = ql.ActualActual(ql.ActualActual.ISDA)
        cr            = prod.credit_spread

        coupon_flows = np.zeros((n_paths, n_periods))
        disc_factors = np.zeros(n_periods)

        for i in range(n_periods):
            start_ql = coupon_dates[i]
            end_ql   = coupon_dates[i + 1]
            accrual  = float(dc_accrual.yearFraction(start_ql, end_ql))
            t_pay    = float(dc_time.yearFraction(pricing_ql, end_ql))

            # 折現因子（含信用利差）
            try:
                df_rsk = curve.discount(end_ql) * np.exp(-cr * t_pay)
            except Exception:
                df_rsk = np.exp(-0.025 * t_pay) * np.exp(-cr * t_pay)
            disc_factors[i] = df_rsk

            # 對應模擬時間索引
            t_start  = float(dc_time.yearFraction(pricing_ql, start_ql))
            t_end    = float(dc_time.yearFraction(pricing_ql, end_ql))
            idx_s    = min(max(int(t_start * self.n_steps_per_year), 0),
                           n_times - 1)
            idx_e    = min(max(int(t_end   * self.n_steps_per_year), 1),
                           n_times - 1)
            if idx_s >= idx_e:
                idx_e = min(idx_s + 1, n_times - 1)

            cms_slice = cms_paths[:, idx_s:idx_e + 1]        # (n_paths, steps)
            in_range  = ((cms_slice >= prod.floor_rate) &
                         (cms_slice <= prod.ceiling_rate))
            frac      = in_range.mean(axis=1)                 # (n_paths,)

            # 首期加入已固定天數
            if i == 0 and prod.in_days > 0:
                total = idx_e - idx_s + 1 + prod.in_days
                frac  = (frac * (idx_e - idx_s + 1) + prod.in_days) / total

            coupon_flows[:, i] = (prod.nominal * prod.coupon_rate
                                  * accrual * frac)

        return coupon_flows, disc_factors

    # ── Layer 3e：LSMC ──────────────────────────

    def _lsmc(self,
              coupon_flows: np.ndarray,
              cms_paths:    np.ndarray,
              disc_factors: np.ndarray) -> Tuple[float, float]:
        """
        Longstaff-Schwartz LSMC（對應文件 p.7-8, p.16）。

        演算法：
        1. 初始化：各路徑現值 = 本金（到期時間點）
        2. 倒推各期：加入票息 → 若為可贖回日，用回歸估計「繼續持有現值」
                    → 若 call_pv ≥ continuation，則執行贖回
        3. 所有路徑在 t=0 的現值平均 → CCDRA 含贖回價格

        回歸基底：[1, CMS, CMS², CMS³]（Laguerre 多項式亦可，此處採冪次方）
        """
        prod         = self.product
        n_paths      = coupon_flows.shape[0]
        n_periods    = coupon_flows.shape[1]
        n_times      = cms_paths.shape[1]
        pricing_ql   = _to_ql(self.market_data.pricing_date)
        dc_time      = ql.ActualActual(ql.ActualActual.ISDA)
        freeze_p     = prod.freeze_periods

        # 初始化：以本金到期現值作為起始
        path_pv   = np.full(n_paths, prod.nominal * disc_factors[-1])
        exercised = np.zeros(n_paths, dtype=bool)

        # 倒推
        for i in range(n_periods - 1, -1, -1):
            # 加入此期票息的折現現值
            path_pv += coupon_flows[:, i] * disc_factors[i]

            # 非可贖回日（封閉期內）跳過
            if i < freeze_p:
                continue

            # 取得此付息日開始時的 CMS 利率（作為回歸狀態變數）
            t_coupon = float(dc_time.yearFraction(
                pricing_ql, self._coupon_dates[i]))
            t_idx = min(max(int(t_coupon * self.n_steps_per_year), 0),
                        n_times - 1)
            cms_t = cms_paths[:, t_idx]                      # (n_paths,)

            # 回歸：繼續持有現值 ~ f(CMS)
            X = np.column_stack([
                np.ones(n_paths),
                cms_t,
                cms_t ** 2,
                cms_t ** 3,
            ])
            try:
                coeffs, _, _, _ = np.linalg.lstsq(X, path_pv, rcond=None)
                cont_val = X @ coeffs
            except Exception:
                cont_val = path_pv.copy()

            # 贖回現值（發行人視角）
            call_pv = prod.call_price * disc_factors[i]

            # 發行人在 call_pv ≥ cont_val 時行使贖回
            do_call = (~exercised) & (call_pv >= cont_val)
            path_pv   = np.where(do_call, call_pv, path_pv)
            exercised = exercised | do_call

        price     = float(np.mean(path_pv))
        call_prob = float(exercised.mean())
        return price, call_prob

    def _price_no_call(self, coupon_flows: np.ndarray,
                       disc_factors: np.ndarray) -> float:
        """純 CDRA 理論價格（無可贖回選擇權）。"""
        pv = coupon_flows * disc_factors            # (n_paths, n_periods)
        pv_total = pv.sum(axis=1)                   # (n_paths,)
        pv_total += self.product.nominal * disc_factors[-1]
        return float(np.mean(pv_total))

    # ── Layer 3f：時程摘要 ───────────────────────

    def _build_schedule_summary(self,
                                cms_paths:    np.ndarray,
                                coupon_flows: np.ndarray,
                                disc_factors: np.ndarray) -> List[Dict]:
        coupon_dates = self._coupon_dates
        n_periods    = len(coupon_dates) - 1
        n_times      = cms_paths.shape[1]
        pricing_ql   = _to_ql(self.market_data.pricing_date)
        dc_time      = ql.ActualActual(ql.ActualActual.ISDA)
        prod         = self.product
        schedule     = []

        for i in range(n_periods):
            t_s   = float(dc_time.yearFraction(pricing_ql, coupon_dates[i]))
            t_e   = float(dc_time.yearFraction(pricing_ql, coupon_dates[i+1]))
            idx_s = min(int(t_s * self.n_steps_per_year), n_times - 1)
            idx_e = min(int(t_e * self.n_steps_per_year), n_times - 1)
            if idx_s >= idx_e:
                idx_e = min(idx_s + 1, n_times - 1)

            cms_slice = cms_paths[:, idx_s:idx_e + 1]
            avg_cms   = float(np.mean(cms_slice)) * 100
            in_range  = ((cms_slice >= prod.floor_rate) &
                         (cms_slice <= prod.ceiling_rate))
            frac      = float(in_range.mean()) * 100
            avg_cpn   = float(np.mean(coupon_flows[:, i]))
            pv        = avg_cpn * disc_factors[i]

            schedule.append({
                'period':        i + 1,
                'start':         str(coupon_dates[i]),
                'end':           str(coupon_dates[i + 1]),
                'avg_cms_pct':   avg_cms,
                'frac_in_range': frac,
                'avg_coupon':    avg_cpn,
                'disc_factor':   disc_factors[i],
                'pv':            pv,
                'callable':      i >= prod.freeze_periods,
            })

        return schedule


# ═══════════════════════════════════════════════════════════════
#  靜態工具函式（模組層級）
# ═══════════════════════════════════════════════════════════════

def _build_corr_matrix(size: int, rho: float, beta: float) -> np.ndarray:
    """
    指數衰減相關矩陣（對應文件 LmLinearExponentialCorrelationModel）：
    ρ_{ij} = ρ∞ + (1−ρ∞)·exp(−β·|i−j|)
    """
    idx  = np.arange(size, dtype=float)
    diff = np.abs(idx[:, None] - idx[None, :])
    corr = rho + (1.0 - rho) * np.exp(-beta * diff)
    np.fill_diagonal(corr, 1.0)
    return corr


def _safe_cholesky(A: np.ndarray) -> np.ndarray:
    """嘗試 Cholesky 分解；若失敗則修正至最近正定矩陣後再分解。"""
    try:
        return np.linalg.cholesky(A)
    except np.linalg.LinAlgError:
        return np.linalg.cholesky(_nearest_pd(A))


def _nearest_pd(A: np.ndarray) -> np.ndarray:
    """求最近正定矩陣（Higham 1988 近似法）。"""
    B  = (A + A.T) / 2.0
    _, s, Vt = np.linalg.svd(B)
    H  = Vt.T @ np.diag(s) @ Vt
    A2 = (B + H) / 2.0
    A3 = (A2 + A2.T) / 2.0
    np.fill_diagonal(A3, 1.0)
    return A3
