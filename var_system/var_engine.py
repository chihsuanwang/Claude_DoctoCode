"""
var_engine.py — 市場風險值（VaR）核心計算引擎
================================================
支援三種業界標準方法：
  1. Historical Simulation (HS)           — 歷史模擬法
  2. Parametric / Variance-Covariance     — 參數法（變異數-共變異數）
  3. Monte Carlo Simulation (MCS)         — 蒙地卡羅模擬法

同時計算：
  - VaR（Value at Risk）
  - CVaR / ES（Conditional VaR / Expected Shortfall）
  - 個別資產 Component VaR
  - Marginal VaR、分散化比率

多市場支援：
  - 自動識別台股（.TW/.TWO）、韓股（.KS/.KQ）、美股（無後綴/其他）
  - 支援 USD、TWD、KRW、HKD、JPY 等貨幣
  - FX 歷史回報疊加至外幣資產回報（Taylor 一階近似）
  - 全部市值統一轉換為基礎貨幣（預設 TWD）計算

學理參考：
  - Jorion, P. (2007). Value at Risk (3rd ed.). McGraw-Hill.
  - Basel III: Minimum Capital Requirements for Market Risk (2019).
  - RiskMetrics™ Technical Document (1996). J.P. Morgan.
"""

import re
import numpy as np
import pandas as pd
from scipy import stats
from scipy.stats import norm
from dataclasses import dataclass, field
from typing import Optional
import warnings

warnings.filterwarnings("ignore")


# ─────────────────────────────────────────────────────────────
# 多市場常數與輔助函式
# ─────────────────────────────────────────────────────────────

# 預設參考匯率（台幣為基礎貨幣，1 外幣 = ? TWD）
# 僅作系統預設，使用者可在 UI 中覆蓋
DEFAULT_SPOT_FX: dict = {
    "TWD": 1.0,
    "USD": 32.5,    # 1 USD ≈ 32.5 TWD
    "KRW": 0.0235,  # 1 KRW ≈ 0.0235 TWD
    "HKD": 4.15,    # 1 HKD ≈ 4.15 TWD
    "JPY": 0.22,    # 1 JPY ≈ 0.22 TWD
    "CNY": 4.45,    # 1 CNY ≈ 4.45 TWD
}

# 市場代號對應表
_SUFFIX_CURRENCY = {
    ".TW":  "TWD",  # 台灣證交所
    ".TWO": "TWD",  # 台灣櫃買中心
    ".KS":  "KRW",  # 韓國 KOSPI
    ".KQ":  "KRW",  # 韓國 KOSDAQ
    ".HK":  "HKD",  # 香港
    ".T":   "JPY",  # 東京
    ".SS":  "CNY",  # 上海
    ".SZ":  "CNY",  # 深圳
}


def detect_asset_currency(ticker: str) -> str:
    """
    依 Yahoo Finance ticker 後綴自動識別資產幣別。

    支援格式：
      純 ticker：       2330.TW、005930.KS、AAPL
      顯示名稱(ticker)：台積電(2330.TW)、Samsung(005930.KS)

    規則：
      *.TW / *.TWO  → TWD（台灣）
      *.KS / *.KQ   → KRW（韓國）
      *.HK          → HKD（香港）
      *.T           → JPY（日本）
      *.SS / *.SZ   → CNY（中國）
      其他           → USD（美國/預設）

    Returns
    -------
    str : 幣別代碼（'TWD', 'USD', 'KRW', ...）
    """
    t = ticker.strip()
    # 若字串結尾為「)」，嘗試提取最後一組括號內的 ticker code
    # 格式：「中文名稱(TICKER)」或「DisplayName(TICKER)」
    m = re.search(r'\(([^()]+)\)\s*$', t)
    if m:
        t = m.group(1).strip()
    t_upper = t.upper()
    for suffix, ccy in _SUFFIX_CURRENCY.items():
        if t_upper.endswith(suffix.upper()):
            return ccy
    return "USD"


def get_market_label(ticker: str) -> str:
    """回傳人類可讀的市場標籤，供 UI 顯示。"""
    ccy = detect_asset_currency(ticker)
    labels = {
        "TWD": "台灣 🇹🇼",
        "KRW": "韓國 🇰🇷",
        "USD": "美國 🇺🇸",
        "HKD": "香港 🇭🇰",
        "JPY": "日本 🇯🇵",
        "CNY": "中國 🇨🇳",
    }
    return labels.get(ccy, ccy)


# ─────────────────────────────────────────────────────────────
# 資料結構
# ─────────────────────────────────────────────────────────────

@dataclass
class Position:
    """單一部位"""
    name: str           # 標的名稱（ticker）
    quantity: float     # 持有數量（正=多頭，負=空頭）
    currency: str = "AUTO"  # 幣別（AUTO = 依 ticker 自動識別）


@dataclass
class VaRResult:
    """VaR 計算結果"""
    method: str
    confidence: float
    horizon: int        # 持有天數

    portfolio_var: float
    portfolio_cvar: float
    portfolio_value: float     # 基礎幣別計
    portfolio_pnl_std: float

    component_var: dict         # 個別資產 VaR（基礎幣別）
    component_cvar: dict
    weights: dict               # 各資產市值權重（以基礎幣別市值計）
    marginal_var: dict          # 邊際 VaR
    diversification_ratio: float  # 分散化比率

    pnl_series: np.ndarray      # 模擬損益序列（基礎幣別，供圖表用）
    returns_df: pd.DataFrame    # 各資產 FX 調整後報酬序列

    # ── 多市場欄位 ──
    base_currency: str = "TWD"
    asset_currencies: dict = field(default_factory=dict)   # {ticker: currency}
    fx_rates_used: dict = field(default_factory=dict)      # {currency: rate}
    local_market_values: dict = field(default_factory=dict)  # 本地幣別市值


# ─────────────────────────────────────────────────────────────
# 工具函式
# ─────────────────────────────────────────────────────────────

def compute_returns(prices: pd.DataFrame, method: str = "log") -> pd.DataFrame:
    """
    計算報酬率序列。
    method: 'log'（對數報酬）或 'pct'（簡單報酬）
    """
    if method == "log":
        returns = np.log(prices / prices.shift(1)).dropna()
    else:
        returns = prices.pct_change().dropna()
    return returns


def scale_var_to_horizon(var_1d: float, horizon: int, method: str = "sqrt") -> float:
    """
    將 1 日 VaR 轉換為 N 日 VaR。
    method: 'sqrt'（平方根法則，Basel II/III 標準）或 'linear'
    """
    if method == "sqrt":
        return var_1d * np.sqrt(horizon)
    return var_1d * horizon


def compute_ewma_cov(returns: pd.DataFrame, lam: float = 0.94) -> pd.DataFrame:
    """
    EWMA（指數加權移動平均）共變異數矩陣。
    lam=0.94 為 RiskMetrics 建議值（日資料）。
    """
    n, k = returns.shape
    cov = returns.iloc[:20].cov().values   # 初始化
    for i in range(20, n):
        r = returns.iloc[i].values.reshape(-1, 1)
        cov = lam * cov + (1 - lam) * (r @ r.T)
    return pd.DataFrame(cov, index=returns.columns, columns=returns.columns)


# ─────────────────────────────────────────────────────────────
# 主計算類別
# ─────────────────────────────────────────────────────────────

class VaRCalculator:
    """
    市場風險值計算引擎（支援多市場、多幣別）。

    使用流程：
        calc = VaRCalculator(
            prices_df,          # 各資產本地幣別收盤價
            positions,          # Position 清單
            base_currency="TWD",
            spot_fx={"USD": 32.5, "KRW": 0.0235},
            fx_prices=fx_df,    # 可選：FX 歷史序列（欄位 = 幣別，值 = 本地幣/基礎幣）
        )
        result = calc.historical(confidence=0.99, horizon=1)
        result = calc.parametric(confidence=0.99, horizon=10)
        result = calc.monte_carlo(confidence=0.99, n_sims=10000)

    多幣別回報調整：
        外幣資產的基礎幣別回報 = r_local + r_FX
        （Taylor 一階近似，等同 RiskMetrics 多貨幣 Delta 方法）
    """

    def __init__(
        self,
        prices: pd.DataFrame,          # index=日期, columns=標的名稱（本地幣別收盤價）
        positions: list,               # list[Position]
        return_method: str = "log",
        ewma: bool = False,
        ewma_lambda: float = 0.94,
        base_currency: str = "TWD",
        fx_prices: Optional[pd.DataFrame] = None,  # FX 歷史（欄位為幣別代碼，值為 1 外幣 = ? 基礎幣）
        spot_fx: Optional[dict] = None,             # 當前即期匯率 {幣別: rate}
    ):
        self.prices = prices.copy()
        self.return_method = return_method
        self.ewma = ewma
        self.ewma_lambda = ewma_lambda
        self.base_currency = base_currency
        self.fx_prices = fx_prices

        # 合併即期匯率（使用者設定優先，以預設補充）
        self.spot_fx = dict(DEFAULT_SPOT_FX)
        if spot_fx:
            self.spot_fx.update(spot_fx)
        # 基礎幣別匯率固定為 1
        self.spot_fx[base_currency] = 1.0

        # 解析 Position，自動識別幣別
        self.positions = {}
        for p in positions:
            ccy = p.currency
            if ccy == "AUTO" or ccy not in self.spot_fx:
                ccy = detect_asset_currency(p.name)
            self.positions[p.name] = Position(name=p.name, quantity=p.quantity, currency=ccy)

        names = list(self.positions.keys())
        missing = [n for n in names if n not in prices.columns]
        if missing:
            raise ValueError(f"價格資料中找不到以下標的：{missing}")

        self.prices = self.prices[names]
        self.names = names
        self.asset_currencies = {n: self.positions[n].currency for n in names}

        # 最新本地幣別價格
        self.latest_prices = self.prices.iloc[-1]

        # 本地幣別市值
        self._local_market_values = {
            n: self.positions[n].quantity * self.latest_prices[n]
            for n in names
        }

        # 基礎幣別市值（乘以即期匯率）
        self.market_values = self._compute_base_market_values()
        self.portfolio_value = sum(abs(v) for v in self.market_values.values())

        # 基礎幣別金額向量（有正負，供矩陣運算）
        self.weights_dollar = np.array([self.market_values[n] for n in names])

        # FX 調整後的報酬序列（外幣資產 = local_ret + fx_ret）
        self.returns = self._build_fx_adjusted_returns()

    # ── 內部：市值與回報計算 ─────────────────────────────────

    def _compute_base_market_values(self) -> dict:
        """計算各資產以基礎幣別計的市值。"""
        result = {}
        for n in self.names:
            local_mv = self.positions[n].quantity * self.latest_prices[n]
            ccy = self.asset_currencies[n]
            rate = self.spot_fx.get(ccy, 1.0)
            result[n] = local_mv * rate
        return result

    def _build_fx_adjusted_returns(self) -> pd.DataFrame:
        """
        建構 FX 調整後的日報酬序列。

        對於外幣資產：
            r_base(t) = r_local(t) + r_FX(t)
        其中 r_FX 來自 fx_prices 中對應幣別欄位的對數報酬。

        若未提供 fx_prices，則使用純本地報酬（FX 風險未納入，UI 會警告）。
        """
        local_ret = compute_returns(self.prices, self.return_method)
        adj_ret = local_ret.copy()

        if self.fx_prices is None or self.fx_prices.empty:
            # 無 FX 歷史：純本地報酬
            self._fx_embedded = False
            return adj_ret

        fx_ret = compute_returns(self.fx_prices, self.return_method)
        self._fx_embedded = True

        for n in self.names:
            ccy = self.asset_currencies[n]
            if ccy == self.base_currency:
                continue  # 基礎幣別資產：不需調整

            # 尋找對應 FX 欄位（精確或子字串比對）
            fx_col = None
            for col in fx_ret.columns:
                if col.strip().upper() == ccy.upper():
                    fx_col = col
                    break
            if fx_col is None:
                continue  # 找不到對應 FX 序列，跳過

            # 取共同日期對齊
            common_idx = adj_ret.index.intersection(fx_ret.index)
            adj_ret.loc[common_idx, n] = (
                adj_ret.loc[common_idx, n].values
                + fx_ret.loc[common_idx, fx_col].values
            )

        return adj_ret

    # ── 1. 歷史模擬法 ────────────────────────────────────────

    def historical(
        self,
        confidence: float = 0.99,
        horizon: int = 1,
        lookback: int = 252,
    ) -> VaRResult:
        """
        Historical Simulation VaR（基礎幣別計）。

        步驟：
        1. 取最近 lookback 個交易日的 FX 調整後報酬
        2. 以今日基礎幣別市值重估每日損益
        3. 取分位數
        """
        ret = self.returns.tail(lookback)

        # 損益序列（基礎幣別金額）
        pnl_series = (ret * self.weights_dollar).sum(axis=1).values

        if horizon > 1:
            pnl_series = pnl_series * np.sqrt(horizon)

        var  = -np.percentile(pnl_series, (1 - confidence) * 100)
        threshold = np.percentile(pnl_series, (1 - confidence) * 100)
        cvar = -pnl_series[pnl_series <= threshold].mean()

        comp_var, comp_cvar = {}, {}
        for n in self.names:
            asset_pnl = ret[n].values * self.market_values[n]
            if horizon > 1:
                asset_pnl = asset_pnl * np.sqrt(horizon)
            comp_var[n]  = -np.percentile(asset_pnl, (1 - confidence) * 100)
            thr = np.percentile(asset_pnl, (1 - confidence) * 100)
            tail = asset_pnl[asset_pnl <= thr]
            comp_cvar[n] = -tail.mean() if len(tail) > 0 else comp_var[n]

        marginal = self._marginal_var_numerical(confidence, horizon, "historical", lookback)

        sum_comp = sum(abs(v) for v in comp_var.values())
        div_ratio = (sum_comp - var) / sum_comp if sum_comp > 0 else 0
        weights_dict = {n: self.market_values[n] / self.portfolio_value for n in self.names}

        return VaRResult(
            method="Historical Simulation",
            confidence=confidence, horizon=horizon,
            portfolio_var=var, portfolio_cvar=cvar,
            portfolio_value=self.portfolio_value,
            portfolio_pnl_std=pnl_series.std() * np.sqrt(horizon),
            component_var=comp_var, component_cvar=comp_cvar,
            weights=weights_dict, marginal_var=marginal,
            diversification_ratio=div_ratio,
            pnl_series=pnl_series, returns_df=ret,
            base_currency=self.base_currency,
            asset_currencies=dict(self.asset_currencies),
            fx_rates_used={c: self.spot_fx.get(c, 1.0)
                           for c in set(self.asset_currencies.values())},
            local_market_values=dict(self._local_market_values),
        )

    # ── 2. 參數法（Variance-Covariance）────────────────────

    def parametric(
        self,
        confidence: float = 0.99,
        horizon: int = 1,
        lookback: int = 252,
    ) -> VaRResult:
        """
        Parametric VaR（Delta-Normal），以基礎幣別計。

        VaR = z_α * σ_portfolio * sqrt(horizon)
        """
        ret = self.returns.tail(lookback)
        z = norm.ppf(confidence)

        if self.ewma:
            cov = compute_ewma_cov(ret, lam=self.ewma_lambda)
        else:
            cov = ret.cov()

        w = self.weights_dollar
        port_var_daily = float(w @ cov.values @ w)
        port_std_daily = np.sqrt(max(port_var_daily, 1e-30))

        var  = z * port_std_daily * np.sqrt(horizon)
        cvar = (norm.pdf(z) / (1 - confidence)) * port_std_daily * np.sqrt(horizon)

        cov_matrix = cov.values
        marginal_contrib = (cov_matrix @ w) / port_std_daily
        comp_var_dollar = w * marginal_contrib * z * np.sqrt(horizon)
        comp_var  = {n: float(cv) for n, cv in zip(self.names, comp_var_dollar)}
        comp_cvar = {
            n: cv * norm.pdf(z) / ((1 - confidence) * z) if z > 0 else 0
            for n, cv in comp_var.items()
        }

        standalone_var = {
            n: z * float(ret[n].std()) * abs(self.market_values[n]) * np.sqrt(horizon)
            for n in self.names
        }
        marginal = {
            n: float(mc) * z * np.sqrt(horizon)
            for n, mc in zip(self.names, marginal_contrib)
        }

        sum_standalone = sum(standalone_var.values())
        div_ratio = (sum_standalone - var) / sum_standalone if sum_standalone > 0 else 0

        pnl_series = (ret * self.weights_dollar).sum(axis=1).values * np.sqrt(horizon)
        weights_dict = {n: self.market_values[n] / self.portfolio_value for n in self.names}

        return VaRResult(
            method="Parametric (Delta-Normal)",
            confidence=confidence, horizon=horizon,
            portfolio_var=var, portfolio_cvar=cvar,
            portfolio_value=self.portfolio_value,
            portfolio_pnl_std=port_std_daily * np.sqrt(horizon),
            component_var=comp_var, component_cvar=comp_cvar,
            weights=weights_dict, marginal_var=marginal,
            diversification_ratio=div_ratio,
            pnl_series=pnl_series, returns_df=ret,
            base_currency=self.base_currency,
            asset_currencies=dict(self.asset_currencies),
            fx_rates_used={c: self.spot_fx.get(c, 1.0)
                           for c in set(self.asset_currencies.values())},
            local_market_values=dict(self._local_market_values),
        )

    # ── 3. 蒙地卡羅模擬法 ───────────────────────────────────

    def monte_carlo(
        self,
        confidence: float = 0.99,
        horizon: int = 1,
        n_sims: int = 10000,
        lookback: int = 252,
        seed: int = 42,
    ) -> VaRResult:
        """
        Monte Carlo Simulation VaR（基礎幣別計）。

        使用 Cholesky 分解保留 FX 調整後報酬的相關結構。
        """
        np.random.seed(seed)
        ret = self.returns.tail(lookback)
        z = norm.ppf(confidence)

        if self.ewma:
            cov = compute_ewma_cov(ret, lam=self.ewma_lambda).values
        else:
            cov = ret.cov().values

        mu = ret.mean().values
        n = len(self.names)

        try:
            L = np.linalg.cholesky(cov)
        except np.linalg.LinAlgError:
            cov += np.eye(n) * 1e-8
            L = np.linalg.cholesky(cov)

        sim_returns = np.zeros((n_sims, n))
        for _ in range(horizon):
            z_rand = np.random.standard_normal((n, n_sims))
            corr_rand = (L @ z_rand).T
            sim_returns += corr_rand + mu

        pnl_series = sim_returns @ self.weights_dollar

        var = -np.percentile(pnl_series, (1 - confidence) * 100)
        threshold = np.percentile(pnl_series, (1 - confidence) * 100)
        cvar = -pnl_series[pnl_series <= threshold].mean()

        tail_mask = pnl_series <= threshold
        comp_var, comp_cvar = {}, {}
        for i, n_name in enumerate(self.names):
            asset_pnl = sim_returns[:, i] * self.market_values[n_name]
            comp_var[n_name]  = -np.percentile(asset_pnl, (1 - confidence) * 100)
            tail_asset = asset_pnl[tail_mask]
            comp_cvar[n_name] = -tail_asset.mean() if len(tail_asset) > 0 else comp_var[n_name]

        marginal = self._marginal_var_numerical(confidence, horizon, "mc", lookback, n_sims, seed)

        sum_comp = sum(abs(v) for v in comp_var.values())
        div_ratio = (sum_comp - var) / sum_comp if sum_comp > 0 else 0
        weights_dict = {n: self.market_values[n] / self.portfolio_value for n in self.names}

        return VaRResult(
            method="Monte Carlo Simulation",
            confidence=confidence, horizon=horizon,
            portfolio_var=var, portfolio_cvar=cvar,
            portfolio_value=self.portfolio_value,
            portfolio_pnl_std=pnl_series.std(),
            component_var=comp_var, component_cvar=comp_cvar,
            weights=weights_dict, marginal_var=marginal,
            diversification_ratio=div_ratio,
            pnl_series=pnl_series, returns_df=ret,
            base_currency=self.base_currency,
            asset_currencies=dict(self.asset_currencies),
            fx_rates_used={c: self.spot_fx.get(c, 1.0)
                           for c in set(self.asset_currencies.values())},
            local_market_values=dict(self._local_market_values),
        )

    # ── 邊際 VaR 數值微分 ────────────────────────────────────

    def _marginal_var_numerical(self, conf, horizon, method, lookback,
                                 n_sims=5000, seed=42, bump=0.01) -> dict:
        """bump 各資產部位 +1%，計算 VaR 變化 → Marginal VaR"""
        result = {}
        base_var = self._quick_var(conf, horizon, method, lookback, n_sims, seed)
        for name in self.names:
            orig_qty = self.positions[name].quantity
            self.positions[name].quantity *= (1 + bump)
            self._refresh_market_values()
            bumped_var = self._quick_var(conf, horizon, method, lookback, n_sims, seed)
            self.positions[name].quantity = orig_qty
            self._refresh_market_values()
            denom = bump * abs(self.market_values[name]) if abs(self.market_values[name]) > 0 else 1.0
            result[name] = (bumped_var - base_var) / denom
        return result

    def _refresh_market_values(self):
        """重新計算市值（Marginal VaR 數值微分使用）。"""
        self._local_market_values = {
            n: self.positions[n].quantity * self.latest_prices[n]
            for n in self.names
        }
        self.market_values = self._compute_base_market_values()
        self.weights_dollar = np.array([self.market_values[n] for n in self.names])

    def _quick_var(self, conf, horizon, method, lookback, n_sims, seed) -> float:
        """快速計算組合 VaR（不產生完整 Result 物件）"""
        ret = self.returns.tail(lookback)
        w = self.weights_dollar
        if method == "historical":
            pnl = (ret * w).sum(axis=1).values * np.sqrt(horizon)
            return -np.percentile(pnl, (1 - conf) * 100)
        elif method == "parametric":
            cov = (ret.cov().values if not self.ewma
                   else compute_ewma_cov(ret, self.ewma_lambda).values)
            std = np.sqrt(max(float(w @ cov @ w), 1e-30))
            return norm.ppf(conf) * std * np.sqrt(horizon)
        else:  # mc
            np.random.seed(seed)
            cov = (ret.cov().values if not self.ewma
                   else compute_ewma_cov(ret, self.ewma_lambda).values)
            mu = ret.mean().values
            try:
                L = np.linalg.cholesky(cov + np.eye(len(w)) * 1e-8)
            except Exception:
                L = np.eye(len(w))
            sim = np.zeros((n_sims, len(w)))
            for _ in range(horizon):
                z_rand = np.random.standard_normal((len(w), n_sims))
                sim += (L @ z_rand).T + mu
            pnl = sim @ w
            return -np.percentile(pnl, (1 - conf) * 100)

    # ── Back-Testing（Kupiec Test）────────────────────────────

    def backtest_kupiec(
        self,
        var_series: pd.Series,
        actual_pnl: pd.Series,
        confidence: float = 0.99,
    ) -> dict:
        """
        Kupiec POF（Proportion of Failures）回測。
        H0: p = 1 - confidence（例外發生機率符合模型預測）
        """
        exceptions = (actual_pnl < -var_series).sum()
        n = len(var_series)
        p_hat = exceptions / n
        p0 = 1 - confidence

        if p_hat == 0:
            lr_stat = -2 * n * np.log(1 - p0)
        elif p_hat == 1:
            lr_stat = -2 * n * np.log(p0)
        else:
            lr_stat = -2 * (
                n * np.log(1 - p0) + exceptions * np.log(p0 / p_hat) +
                (n - exceptions) * np.log((1 - p0) / (1 - p_hat))
            )

        p_value = 1 - stats.chi2.cdf(lr_stat, df=1)
        reject = p_value < 0.05

        return {
            "n_observations": n,
            "n_exceptions": int(exceptions),
            "exception_rate": p_hat,
            "expected_rate": p0,
            "lr_statistic": lr_stat,
            "p_value": p_value,
            "reject_H0": reject,
            "verdict": "⚠️ 模型例外超標，建議重新校準" if reject else "✓ 模型通過 Kupiec 回測",
        }


# ─────────────────────────────────────────────────────────────
# 快速使用範例（直接執行此檔案時）
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import yfinance as yf

    # 多市場範例：台股 + 美股 + 韓股
    tw_tickers = ["2330.TW", "2454.TW"]
    us_tickers = ["AAPL", "MSFT"]
    kr_tickers = ["005930.KS"]

    all_tickers = tw_tickers + us_tickers + kr_tickers
    prices = yf.download(all_tickers, start="2022-01-01", end="2024-12-31",
                         progress=False)["Close"].dropna()

    # FX 匯率歷史（USD/TWD、KRW/TWD）
    fx_raw = yf.download(["TWD=X", "KRWTWD=X"], start="2022-01-01",
                         end="2024-12-31", progress=False)["Close"].dropna()
    fx_raw.columns = ["KRW", "USD"]  # 重命名為幣別代碼

    spot_fx = {"USD": 32.5, "KRW": 0.0235}

    positions = [
        Position("2330.TW", 1000),
        Position("2454.TW",  500),
        Position("AAPL",     100),
        Position("MSFT",      50),
        Position("005930.KS", 10),
    ]

    calc = VaRCalculator(
        prices=prices,
        positions=positions,
        base_currency="TWD",
        spot_fx=spot_fx,
        fx_prices=fx_raw,
    )

    r = calc.historical(confidence=0.99, horizon=1)
    print(f"HS VaR(99%, 1d) = TWD {r.portfolio_var:,.0f}")
    print(f"資產幣別：{r.asset_currencies}")
    print(f"使用匯率：{r.fx_rates_used}")
