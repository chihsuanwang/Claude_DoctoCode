# CCDRA 評價模型實作計畫

**產品名稱**：可贖回 CMS 連結每日區間計息債券 (Callable CMS-Linked Daily Range Accrual Note, CCDRA)  
**評價方法**：LIBOR Market Model (LMM) + Least Square Monte Carlo (LSMC)  
**實作語言**：Python (QuantLib-Python)

---

## 一、核心數學邏輯與定價流程

### 1.1 產品結構

CCDRA 每一計息區間的區間計息額為：

```
S_i = (Q_i / n_i) × Σ I_{B < R(T_{i-1} + t_j) < U}
```

- `n_i`：第 i 計息區間的日數
- `R(t)`：t 時點的參考利率（2Y CMS 利率）
- `B`（Floor）= 0.0%，`U`（Ceiling）= 4.25%
- 付息頻率：Quarterly，計息基礎：30/360

CCDRA = CDRA（每日區間計息）+ 發行人買回選擇權（凍結期 1 年後可行使）

### 1.2 LMM 模型動態方程

在 Spot LIBOR Martingale 測度下，遠期利率滿足：

```
dL_i(t) = μ_i(L(t), t) · (L_i(t) + α_i) dt
         + σ_i(t) · (L_i(t) + α_i) · dW_i(t)
```

漂移項（drift）由無套利條件決定：

```
μ_i(L(t), t) = Σ_{j=η(t)}^{i}  [L̃_j(t)·δ / (1 + L_j(t)·δ)] · σ_{i,j}(t)
```

遠期利率擴散函數（對數形式）：

```
log(L_i(t)) = log(L_i(s)) - (1/2)·σ_{i,i}(s,t)
              + Σ_{r=1}^{F} A_{i,r} · (e_{r,i} + Z_r)
```

- `σ_i(t)`：ABCD 波動度函數（`LmExtLinearExponentialVolModel`）
- `ρ_{i,j}`：指數相關係數矩陣（`LmLinearExponentialCorrelationModel`）
- `A`：共變異數矩陣的方根分解因子矩陣

### 1.3 CMS 利率計算（M. Joshi 改良 Schlogl 內插法）

由模擬出的遠期 LIBOR 路徑，加權推算 x 年期 CMS 利率：

```
SR̃_{t,x}^n(s) = Σ_{j=0}^{x-1}  w_j · L̃_j^n(s + j·δ)
```

權重 `w_j` 由利率交換的固定端與浮動端折現因子拆解決定。

### 1.4 區間計息內插法（Interval Interpolation）

令 `R_H = max(R(T_{i-1}), R(T_i))`，`R_L = min(R(T_{i-1}), R(T_i))`：

```
Ratio_i = 1,                                   若 B ≤ R_L ≤ R_H ≤ U
         = 0,                                   若 R_H ≤ B 或 R_L ≥ U
         = [Min(U, R_H) - Max(B, R_L)] / (R_H - R_L),  otherwise
```

此為模型簡化近似，用連續線性比例取代每日逐一判斷。

### 1.5 完整定價流程（7 步驟）

```
Step 1  建立利率期間結構（Bootstrapping）
Step 2  讀取 Swaption Vol 市場資料
Step 3  校準 LMM 模型參數（ABCD + Correlation → Calibration）
Step 4  Monte Carlo 模擬遠期利率路徑（LMM Evolution）
Step 5  由 LIBOR 路徑推算每條路徑的 CMS 利率
Step 6  套用區間內插法計算每期現金流（含本金返還）
Step 7  LSMC 逆向歸納，判斷最佳行權時點，計算 CCDRA 理論價格
```

---

## 二、所需 QuantLib 核心物件

| 類別 | QuantLib 物件 | 用途 |
|------|--------------|------|
| **市場資料** | `SimpleQuote`, `Handle<Quote>` | 利率報價的可觀測包裝，支援 observer pattern |
| **期間結構** | `RelinkableHandle<YieldTermStructure>` | 可熱插拔的殖利率曲線 handle |
| **曲線建構** | `PiecewiseYieldCurve<Discount, LogLinear>` | Bootstrapping 建立折現曲線 |
| **利率輔助** | `DepositRateHelper`, `SwapRateHelper` | 提供 Bootstrapping 的市場錨點 |
| **指標** | `USDLibor(Period('3M'))` | LMM 模擬的基礎 LIBOR 指標 |
| **CMS 指標** | `EuriborSwapIsdaFixA` / `SwapIndex` | 計算 CMS swap rate |
| **LMM 流程** | `LiborForwardModelProcess` | 核心隨機過程，驅動 LIBOR 路徑演化 |
| **波動度模型** | `LmExtLinearExponentialVolModel` | ABCD 參數化波動度結構 |
| **相關係數模型** | `LmLinearExponentialCorrelationModel` | 指數遞減相關係數矩陣 |
| **LMM 模型** | `LiborForwardModel` | 組合 Process + Vol + Corr 的主體模型 |
| **校準輔助** | `SwaptionHelper` | 市場 Swaption vol 的校準錨點 |
| **校準引擎** | `LfmSwaptionEngine` | 用 LMM 為 Swaption helper 定價 |
| **最佳化** | `LevenbergMarquardt`, `EndCriteria` | 非線性參數最佳化 |
| **日期計算** | `Thirty360`, `Schedule`, `Calendar` | 計息日曆與日期慣例 |
| **數值工具** | `Matrix`, `Array` | 共變異數矩陣計算、隨機數向量 |
| **亂數引擎** | `MersenneTwisterUniformRng` / Sobol | Monte Carlo 路徑生成 |

---

## 三、開發模組切分（依 QuantLib 解耦原則）

QuantLib 的設計哲學：**MarketData → Model → Instrument → Engine** 四層嚴格解耦。每個模組只依賴上游的 `Handle` 介面，不直接持有物件，確保市場資料更新時自動傳播。

---

### Module 1：市場資料與利率期間結構（Market Data & Term Structure）

**對應 QuantLib 層**：MarketData Layer

**職責**：
- 讀取 Bloomberg CSV（Deposit + Swap）並建立 `SwapRateHelper` / `DepositRateHelper`
- 執行 Bootstrapping，產出 `YieldTermStructure`（折現曲線 + 遠期曲線）
- 讀取 Swaption Vol 矩陣，包裝為 `Handle<Quote>` 的二維結構
- 對外只暴露 `RelinkableHandle<YieldTermStructure>` 與 vol 矩陣

**輸入**：CSV 利率資料（Term, InstType, Mid）、Swaption Vol 矩陣（Opt × Swap tenor）  
**輸出**：`discountHandle`、`forwardHandle`、`swaptionVolMatrix`

**關鍵物件**：
```
PiecewiseYieldCurve → RelinkableHandle<YieldTermStructure>
DepositRateHelper, SwapRateHelper
SwaptionVolatilityMatrix (或 SwaptionVolatilityStructure)
```

**解耦說明**：此模組不知道任何模型。下游模組透過 `Handle` 取用，市場資料更新時自動觸發重算。

---

### Module 2：LMM 模型建構與校準（LMM Model & Calibration）

**對應 QuantLib 層**：Model Layer

**職責**：
- 建立 `LiborForwardModelProcess`（含 size、index 設定）
- 建立 ABCD 波動度模型（`LmExtLinearExponentialVolModel`）
- 建立指數相關係數模型（`LmLinearExponentialCorrelationModel`）
- 組合為 `LiborForwardModel`
- 以 `SwaptionHelper` + `LfmSwaptionEngine` 執行市場校準
- 輸出校準後的模型參數（a, b, c, d, rho, beta）

**輸入**：`forwardHandle`（Module 1）、`swaptionVolMatrix`（Module 1）  
**輸出**：已校準的 `LiborForwardModel` 物件（含所有參數）

**關鍵物件**：
```
USDLibor(3M) → LiborForwardModelProcess
LmExtLinearExponentialVolModel (a, b, c, d)
LmLinearExponentialCorrelationModel (rho, beta)
LiborForwardModel
SwaptionHelper × N → List<CalibrationHelper>
LfmSwaptionEngine
LevenbergMarquardt + EndCriteria
```

**解耦說明**：此模組只消費 `Handle<YieldTermStructure>`，不直接操作曲線物件。模型校準完成後，以抽象 `model` 物件傳給下游，下游不需知道校準細節。

---

### Module 3：蒙地卡羅路徑生成與現金流計算（MC Evolution & Cash Flow Engine）

**對應 QuantLib 層**：Instrument / Path Generation Layer

**職責**：
- 依 LMM 擴散方程執行 Monte Carlo 路徑模擬（N paths × M steps）
- 計算每步驟的漂移項（drift）與共變異數矩陣（covariance structure）
- 由 LIBOR 路徑推算每條路徑、每個時間點的 2Y CMS 利率
- 套用區間內插法（Ratio 公式）計算每期計息天數佔比
- 計算各路徑各期的現金流向量（`Payoff_{t,j}`），含本金返還

**輸入**：已校準的 `LiborForwardModel`（Module 2）、契約參數（Floor, Ceiling, Coupon, Schedule）  
**輸出**：`paths × timesteps` 的現金流矩陣、對應的 CMS 利率矩陣、折現因子向量

**關鍵物件**：
```
MersenneTwisterUniformRng → BoxMullerGaussianRng → 路徑
Matrix (GCOV): 共變異數矩陣
Array (drifts, logFwds): 每步更新遠期利率
自訂 CmsRateCalculator: 由 LIBOR 推 CMS（Schlogl 內插）
自訂 RangeAccrualInterpolator: 計算 Ratio_i
自訂 CashFlowMatrix: 各路徑現金流
```

**解耦說明**：此模組不知道 LSMC 演算法，只負責產出「原始現金流路徑」。CMS 計算邏輯封裝在獨立函數中，可獨立測試。折現因子由 `discountHandle` 查詢，不直接參考 model 內部。

---

### Module 4：LSMC 可贖回定價引擎（Callable Pricing via LSMC）

**對應 QuantLib 層**：PricingEngine Layer

**職責**：
- 接收 Module 3 產出的現金流矩陣
- 從最終到期日逆向歸納（backward induction）
- 以 CMS 利率為回歸基底函數（1, x, x², x³），對每個時間點進行最小平方回歸
- 比較「繼續持有期望值 E[CCDRA]」與「立即贖回價格 Call Price」
- 更新各路徑的最佳行使時點
- 對所有路徑的 t=0 理論價值取平均，輸出 CCDRA 理論價格

**輸入**：現金流矩陣（Module 3）、CMS 利率矩陣（Module 3）、折現因子（Module 1）、Call Price  
**輸出**：CCDRA 理論價格（NPV）、Call Value、各路徑最佳行使時點

**關鍵物件**：
```
Matrix MX (OBS × 4): 回歸設計矩陣 [1, x, x², x³]
Matrix MY (OBS × 1): 目標向量（繼續持有折現現金流）
Matrix MB = (MXT·MX)^{-1}·MXT·MY: 最小平方解
自訂 LsmcCallableEngine: 封裝逆向歸納邏輯
```

**解耦說明**：此模組不知道利率模型細節，只操作數值矩陣。回歸基底函數可抽換（例如改用 Laguerre polynomials），不影響其他模組。最終輸出為純量 NPV，介面極簡。

---

## 四、模組依賴關係圖

```
┌─────────────────────────────────────────────────────────┐
│  Module 1: Market Data & Term Structure                  │
│  DepositHelper / SwapHelper → PiecewiseYieldCurve        │
│  SwaptionVolMatrix                                        │
└────────────────────┬────────────────────────────────────┘
                     │ RelinkableHandle<YTS> + VolMatrix
                     ▼
┌─────────────────────────────────────────────────────────┐
│  Module 2: LMM Model & Calibration                       │
│  LiborForwardModelProcess + ABCD Vol + Corr              │
│  → LiborForwardModel (calibrated)                        │
└────────────────────┬────────────────────────────────────┘
                     │ calibrated model object
                     ▼
┌─────────────────────────────────────────────────────────┐
│  Module 3: MC Evolution & Cash Flow Engine               │
│  Path generation → CMS calc → Range Accrual → CF matrix  │
└────────────────────┬────────────────────────────────────┘
                     │ CF matrix + CMS matrix + discount factors
                     ▼
┌─────────────────────────────────────────────────────────┐
│  Module 4: LSMC Callable Pricing Engine                  │
│  Backward induction → Regression → Call decision → NPV   │
└─────────────────────────────────────────────────────────┘
```

---

## 五、範例契約參數（實作測試基準）

| 參數 | 數值 |
|------|------|
| 評價日 (Today) | 2022/3/31 |
| 發行日 (Issue Date) | 2022/3/31 |
| 到期日 (Maturity) | 2027/3/31 |
| 本金 (Nominal) | USD 10,000,000 |
| 票面利率 (Coupon) | 1.65% |
| 下限利率 (Floor B) | 0.00% |
| 上限利率 (Ceiling U) | 4.25% |
| 參考指標 | 2Y CMS (USSW2) |
| 付息頻率 | Quarterly |
| 計息基礎 | 30/360 |
| 凍結期 | 1 年 |
| 贖回價格 | 100.00 |
| LMM 基礎 LIBOR | USD Libor 3M |
| 預期 CCDRA 價格 | ≈ 82.02（文件範例） |

---

## 六、開發里程碑順序

```
Milestone 1  Module 1 完成：可從 CSV 建立折現曲線 + Swaption Vol 矩陣
Milestone 2  Module 2 完成：LMM 成功 Calibrate，輸出 a/b/c/d/rho/beta 參數
Milestone 3  Module 3 完成：N=10,000 路徑的遠期利率演化 + CMS + 現金流矩陣
Milestone 4  Module 4 完成：LSMC 計算 CCDRA 理論價格，對比文件範例 ≈ 82.02
```
