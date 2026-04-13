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
  - Greeks：Delta（一階）
  - Marginal VaR、Incremental VaR

學理參考：
  - Jorion, P. (2007). Value at Risk (3rd ed.). McGraw-Hill.
  - Basel III: Minimum Capital Requirements for Market Risk (2019).
"""

import numpy as np
import pandas as pd
from scipy import stats
from scipy.stats import norm
from dataclasses import dataclass, field
from typing import Optional
import warnings

warnings.filterwarnings("ignore")


# ─────────────────────────────────────────────────────────────
# 資料結構
# ─────────────────────────────────────────────────────────────

@dataclass
class Position:
    """單一部位"""
    name: str           # 標的名稱
    quantity: float     # 持有數量（正=多頭，負=空頭）
    currency: str = "USD"


@dataclass
class VaRResult:
    """VaR 計算結果"""
    method: str
    confidence: float
    horizon: int        # 持有天數

    portfolio_var: float
    portfolio_cvar: float
    portfolio_value: float
    portfolio_pnl_std: float

    component_var: dict         # 個別資產 VaR
    component_cvar: dict
    weights: dict               # 各資產市值權重
    marginal_var: dict          # 邊際 VaR
    diversification_ratio: float  # 分散化比率

    pnl_series: np.ndarray      # 模擬損益序列（供圖表用）
    returns_df: pd.DataFrame    # 各資產報酬序列


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
    method: 'sqrt'（平方根法則，Basel II/III 標準）或 'exact'
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
    市場風險值計算引擎。

    使用流程：
        calc = VaRCalculator(prices_df, positions)
        result = calc.historical(confidence=0.99, horizon=1)
        result = calc.parametric(confidence=0.99, horizon=10)
        result = calc.monte_carlo(confidence=0.99, n_sims=10000)
    """

    def __init__(
        self,
        prices: pd.DataFrame,          # index=日期, columns=標的名稱
        positions: list[Position],
        return_method: str = "log",
        ewma: bool = False,
        ewma_lambda: float = 0.94,
    ):
        self.prices = prices.copy()
        self.positions = {p.name: p for p in positions}
        self.return_method = return_method
        self.ewma = ewma
        self.ewma_lambda = ewma_lambda

        # 對齊欄位：只保留有部位的標的
        names = [p.name for p in positions]
        missing = [n for n in names if n not in prices.columns]
        if missing:
            raise ValueError(f"價格資料中找不到以下標的：{missing}")
        self.prices = self.prices[names]

        # 計算日對數報酬
        self.returns = compute_returns(self.prices, method=return_method)

        # 最新價格
        self.latest_prices = self.prices.iloc[-1]

        # 各資產市值（含空頭負值）
        self.market_values = {
            n: self.positions[n].quantity * self.latest_prices[n]
            for n in names
        }
        self.portfolio_value = sum(abs(v) for v in self.market_values.values())

        # 各資產金額權重（有正負）
        self.weights_dollar = np.array(
            [self.market_values[n] for n in names]
        )
        self.names = names

    # ── 1. 歷史模擬法 ────────────────────────────────────────

    def historical(
        self,
        confidence: float = 0.99,
        horizon: int = 1,
        lookback: int = 252,
    ) -> VaRResult:
        """
        Historical Simulation VaR。

        步驟：
        1. 取最近 lookback 個交易日的日報酬
        2. 用今日市值重估每日的損益（Full Revaluation）
        3. 取分位數
        """
        ret = self.returns.tail(lookback)

        # 損益序列（以金額計）
        # P&L_t = Σ_i  w_i * r_i,t
        pnl_series = (ret * self.weights_dollar).sum(axis=1).values

        # 多日持有期：若 horizon>1，用 sqrt 法或直接加總相鄰日
        if horizon > 1:
            pnl_series = pnl_series * np.sqrt(horizon)

        # VaR（損失為正值）
        var = -np.percentile(pnl_series, (1 - confidence) * 100)

        # CVaR / ES
        threshold = np.percentile(pnl_series, (1 - confidence) * 100)
        cvar = -pnl_series[pnl_series <= threshold].mean()

        # Component VaR（各資產歷史模擬）
        comp_var, comp_cvar = {}, {}
        for n in self.names:
            asset_pnl = ret[n].values * self.market_values[n]
            if horizon > 1:
                asset_pnl = asset_pnl * np.sqrt(horizon)
            comp_var[n]  = -np.percentile(asset_pnl, (1 - confidence) * 100)
            thr = np.percentile(asset_pnl, (1 - confidence) * 100)
            comp_cvar[n] = -asset_pnl[asset_pnl <= thr].mean() if len(asset_pnl[asset_pnl <= thr]) > 0 else comp_var[n]

        # 邊際 VaR（數值微分）
        marginal = self._marginal_var_numerical(confidence, horizon, "historical", lookback)

        # 分散化比率
        sum_comp = sum(comp_var.values())
        div_ratio = (sum_comp - var) / sum_comp if sum_comp > 0 else 0

        weights_dict = {n: self.market_values[n] / self.portfolio_value for n in self.names}

        return VaRResult(
            method="Historical Simulation",
            confidence=confidence,
            horizon=horizon,
            portfolio_var=var,
            portfolio_cvar=cvar,
            portfolio_value=self.portfolio_value,
            portfolio_pnl_std=pnl_series.std() * np.sqrt(horizon),
            component_var=comp_var,
            component_cvar=comp_cvar,
            weights=weights_dict,
            marginal_var=marginal,
            diversification_ratio=div_ratio,
            pnl_series=pnl_series,
            returns_df=ret,
        )

    # ── 2. 參數法（Variance-Covariance）────────────────────

    def parametric(
        self,
        confidence: float = 0.99,
        horizon: int = 1,
        lookback: int = 252,
    ) -> VaRResult:
        """
        Parametric VaR（Delta-Normal）。

        假設：損益服從常態分布。
        VaR = z_α * σ_portfolio * sqrt(horizon)
        其中 z_α = norm.ppf(confidence)
        """
        ret = self.returns.tail(lookback)
        z = norm.ppf(confidence)

        # 共變異數矩陣
        if self.ewma:
            cov = compute_ewma_cov(ret, lam=self.ewma_lambda)
        else:
            cov = ret.cov()

        w = self.weights_dollar  # (n,) 向量

        # 組合變異數：σ²_p = w' Σ w
        port_var_daily = float(w @ cov.values @ w)
        port_std_daily = np.sqrt(port_var_daily)

        # N 日 VaR
        var  = z * port_std_daily * np.sqrt(horizon)
        cvar = (norm.pdf(z) / (1 - confidence)) * port_std_daily * np.sqrt(horizon)

        # Component VaR = (Σw)_i * z * sqrt(horizon)
        cov_matrix = cov.values
        marginal_contrib = (cov_matrix @ w) / port_std_daily   # ∂σ_p/∂w_i
        comp_var_dollar = w * marginal_contrib * z * np.sqrt(horizon)
        comp_var = {n: float(cv) for n, cv in zip(self.names, comp_var_dollar)}
        comp_cvar = {n: cv * norm.pdf(z) / ((1 - confidence) * z) if z > 0 else 0
                     for n, cv in comp_var.items()}

        # 個別資產（standalone）VaR
        standalone_var = {
            n: z * float(ret[n].std()) * abs(self.market_values[n]) * np.sqrt(horizon)
            for n in self.names
        }

        # 邊際 VaR
        marginal = {n: float(mc) * z * np.sqrt(horizon)
                    for n, mc in zip(self.names, marginal_contrib)}

        # 分散化比率
        sum_standalone = sum(standalone_var.values())
        div_ratio = (sum_standalone - var) / sum_standalone if sum_standalone > 0 else 0

        # 模擬損益序列（供圖表）
        mean_ret = ret.mean().values
        pnl_series = (ret * self.weights_dollar).sum(axis=1).values * np.sqrt(horizon)

        weights_dict = {n: self.market_values[n] / self.portfolio_value for n in self.names}

        return VaRResult(
            method="Parametric (Delta-Normal)",
            confidence=confidence,
            horizon=horizon,
            portfolio_var=var,
            portfolio_cvar=cvar,
            portfolio_value=self.portfolio_value,
            portfolio_pnl_std=port_std_daily * np.sqrt(horizon),
            component_var=comp_var,
            component_cvar=comp_cvar,
            weights=weights_dict,
            marginal_var=marginal,
            diversification_ratio=div_ratio,
            pnl_series=pnl_series,
            returns_df=ret,
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
        Monte Carlo Simulation VaR。

        步驟：
        1. 估計共變異數矩陣（或 EWMA）
        2. Cholesky 分解取得相關性結構
        3. 模擬 n_sims 條路徑
        4. 取分位數
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

        # Cholesky 分解
        try:
            L = np.linalg.cholesky(cov)
        except np.linalg.LinAlgError:
            # 若矩陣非正定，加微小擾動
            cov += np.eye(n) * 1e-8
            L = np.linalg.cholesky(cov)

        # 模擬 horizon 日累積報酬
        sim_returns = np.zeros((n_sims, n))
        for _ in range(horizon):
            z_rand = np.random.standard_normal((n, n_sims))
            corr_rand = (L @ z_rand).T   # (n_sims, n)
            sim_returns += corr_rand + mu

        # 組合損益（金額）
        pnl_series = sim_returns @ self.weights_dollar

        # VaR & CVaR
        var = -np.percentile(pnl_series, (1 - confidence) * 100)
        threshold = np.percentile(pnl_series, (1 - confidence) * 100)
        cvar = -pnl_series[pnl_series <= threshold].mean()

        # Component VaR（各資產在尾端場景的均值損失）
        tail_mask = pnl_series <= threshold
        comp_var, comp_cvar = {}, {}
        for i, n_name in enumerate(self.names):
            asset_pnl = sim_returns[:, i] * self.market_values[n_name]
            comp_var[n_name]  = -np.percentile(asset_pnl, (1 - confidence) * 100)
            tail_asset = asset_pnl[tail_mask]
            comp_cvar[n_name] = -tail_asset.mean() if len(tail_asset) > 0 else comp_var[n_name]

        # 邊際 VaR（數值微分）
        marginal = self._marginal_var_numerical(confidence, horizon, "mc",
                                                 lookback, n_sims, seed)

        sum_comp = sum(comp_var.values())
        div_ratio = (sum_comp - var) / sum_comp if sum_comp > 0 else 0
        weights_dict = {n: self.market_values[n] / self.portfolio_value for n in self.names}

        return VaRResult(
            method="Monte Carlo Simulation",
            confidence=confidence,
            horizon=horizon,
            portfolio_var=var,
            portfolio_cvar=cvar,
            portfolio_value=self.portfolio_value,
            portfolio_pnl_std=pnl_series.std(),
            component_var=comp_var,
            component_cvar=comp_cvar,
            weights=weights_dict,
            marginal_var=marginal,
            diversification_ratio=div_ratio,
            pnl_series=pnl_series,
            returns_df=ret,
        )

    # ── 邊際 VaR 數值微分 ────────────────────────────────────

    def _marginal_var_numerical(self, conf, horizon, method, lookback,
                                 n_sims=5000, seed=42, bump=0.01) -> dict:
        """bump 各資產部位 +1%，計算 VaR 變化 / bump → Marginal VaR"""
        result = {}
        base_var = self._quick_var(conf, horizon, method, lookback, n_sims, seed)
        for name in self.names:
            orig_qty = self.positions[name].quantity
            self.positions[name].quantity *= (1 + bump)
            self._refresh_market_values()
            bumped_var = self._quick_var(conf, horizon, method, lookback, n_sims, seed)
            self.positions[name].quantity = orig_qty
            self._refresh_market_values()
            result[name] = (bumped_var - base_var) / (bump * abs(self.market_values[name]))
        return result

    def _refresh_market_values(self):
        self.market_values = {
            n: self.positions[n].quantity * self.latest_prices[n]
            for n in self.names
        }
        self.weights_dollar = np.array([self.market_values[n] for n in self.names])

    def _quick_var(self, conf, horizon, method, lookback, n_sims, seed) -> float:
        """快速計算 VaR（不產生完整 Result 物件）"""
        ret = self.returns.tail(lookback)
        w = self.weights_dollar
        if method == "historical":
            pnl = (ret * w).sum(axis=1).values * np.sqrt(horizon)
            return -np.percentile(pnl, (1 - conf) * 100)
        elif method == "parametric":
            cov = ret.cov().values if not self.ewma else compute_ewma_cov(ret, self.ewma_lambda).values
            std = np.sqrt(float(w @ cov @ w))
            return norm.ppf(conf) * std * np.sqrt(horizon)
        else:  # mc
            np.random.seed(seed)
            cov = ret.cov().values if not self.ewma else compute_ewma_cov(ret, self.ewma_lambda).values
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
    tickers = ["AAPL", "MSFT", "GOOGL"]
    prices = yf.download(tickers, start="2022-01-01", end="2024-12-31", progress=False)["Close"]
    positions = [
        Position("AAPL",  100),
        Position("MSFT",   50),
        Position("GOOGL",  30),
    ]
    calc = VaRCalculator(prices, positions)
    r = calc.historical(confidence=0.99, horizon=1)
    print(f"HS VaR(99%, 1d) = ${r.portfolio_var:,.2f}")
    r2 = calc.parametric(confidence=0.99, horizon=10)
    print(f"Param VaR(99%, 10d) = ${r2.portfolio_var:,.2f}")
