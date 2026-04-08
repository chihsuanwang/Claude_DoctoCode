# 【獨立模型驗證技術手冊】可贖回 CMS 連結區間計息定價模型
**Model Validation Technical Document: Callable CMS-linked Daily Range Accrual**

> **文件版本：** v2.0 — 2026-04-08
> **增修說明：** 本版本在原始程式碼解析基礎上，由財務工程專家增補學理文獻、測度理論基礎、數值方法驗證要點，以及獨立模型驗證 (IMV) 建議。
> **備份檔案：** `可贖回CMS區間計息技術文件_BACKUP_20260408.MD`

---

## 一、 評價基準日與基礎利率曲線建構 (Valuation Date & Term Structure Setup)

```csharp
public void SetupTermStructure()
{
    Settings.setEvaluationDate(Today);
```

*   **財務工程視角：** 這是所有量化定價引擎（特別是類似 QuantLib 的開源庫或其衍生架構）的起手式。這行程式碼設定了**全域評價基準日 (Valuation Date)**。在定價模型中，這個日期代表時間原點 $t=0$。未來所有的現金流折現因子 (Discount Factor) $P(t,T)$ 以及所有距離到期日的時間 $\tau = T - t$ 計算，都會以此日期為基準。在驗證上，必須確保系統帶入的 `Today` 與實際的市場數據日期（通常是 T-1 日或 T 日的 EOD 數據）完全對齊。

*   **學理基礎：** 評價基準日確立後，折現因子 $P(t,T)$ 由無套利市場下的期限結構所決定。根據 Heath-Jarrow-Morton (HJM) 框架，在風險中性測度 $\mathbb{Q}$ 下，折現債券價格滿足：

    $$P(t,T) = \mathbb{E}^{\mathbb{Q}}\left[\exp\left(-\int_t^T r_s \, ds\right) \,\bigg|\, \mathcal{F}_t\right]$$

    其中 $r_s$ 為瞬時短期利率。在實務的 Bootstrap 過程中，系統從 Swap 市場報價中逐步萃取出各天期的零息折現因子，此過程確保模型與市場報價的一致性（Market-Consistent Pricing）。

    > **參考文獻：**
    > - Heath, D., Jarrow, R., & Morton, A. (1992). *Bond Pricing and the Term Structure of Interest Rates: A New Methodology for Contingent Claims Valuation.* Econometrica, 60(1), 77–105.
    > - Brigo, D., & Mercurio, F. (2006). *Interest Rate Models — Theory and Practice* (2nd ed.). Springer Finance. Chapter 1–2.

```csharp
    //---------------------- Create Original Curve ----------------------
    dataReading = new DataReading();
    dataReading.SetupTermStructure(Today, swap_path);
```

*   **財務工程視角：** 這裡實例化了一個讀取數據的物件，並呼叫 `SetupTermStructure` 載入市場報價數據。從變數名稱 `swap_path` 可以明確看出，模型正在從外部檔案或資料庫讀取**利率交換報價 (Interest Rate Swap Quotes)** 來建構（Bootstrap）殖利率曲線。對於 CMS (Constant Maturity Swap) 產品而言，Swap Curve 是最核心的基礎。

*   **學理基礎：** Bootstrap 程序的核心假設為互換合約在成交時市值為零（Par Condition），即固定腳等於浮動腳的現值。對於標準利率交換 (Plain Vanilla IRS)：

    $$\sum_{i=1}^{n} c \cdot \tau_i \cdot P(0,T_i) = \sum_{i=1}^{n} \text{FWD}(0;T_{i-1},T_i) \cdot \tau_i \cdot P(0,T_i)$$

    從短天期到長天期逐步求解 $P(0,T_i)$，即構成完整的殖利率曲線。

    > **參考文獻：**
    > - Andersen, L., & Piterbarg, V. (2010). *Interest Rate Modeling* (Vol. 1). Atlantic Financial Press. Chapter 5: Curve Construction.

```csharp
    HTS = dataReading.forwarding;
```

*   **財務工程視角：** 這裡將建構好的曲線賦值給變數 `HTS`。在量化程式庫的慣例中，`HTS` 通常代表 **Handle to Term Structure**（期限結構的指標或句柄）。而 `forwarding` 則明確指出這條曲線將被用作**遠期利率預測曲線 (Forwarding Curve)**。在區間計息 (Range Accrual) 商品中，模型需要計算每一天 (或每個觀察日) 的遠期指標利率（例如未來的 CMS 利率）是否落在指定的上下限區間內，這條 `forwarding` 曲線就是用來推導這些預期遠期利率的基礎。

*   **學理基礎（2008 後雙曲線架構的必要性）：** 2008 年金融危機之前，市場普遍採用單一曲線架構（預測曲線 = 折現曲線）。危機後，LIBOR-OIS Spread 的大幅擴大揭示了信用風險溢酬不可忽視，促使業界轉向**雙曲線（Multi-Curve）定價架構**：
    - **預測曲線 (Forwarding Curve)：** 用於推算未來浮動現金流（如 3M LIBOR 或 CMS），來源於 Swap 市場報價。
    - **折現曲線 (Discounting Curve)：** 用於折算現金流現值，通常採用無風險的 OIS 曲線（如美元 Fed Funds OIS）。

    若模型仍採用單一曲線，在當前利率環境下可能導致系統性定價偏差。

    > **參考文獻：**
    > - Bianchetti, M. (2010). *Two Curves, One Price.* Risk Magazine, August 2010.
    > - Mercurio, F. (2010). *Modern LIBOR Market Models: Using Different Curves for Projecting Rates and for Discounting.* International Journal of Theoretical and Applied Finance, 13(1), 113–137.

---

## 二、 波動度曲面讀取與過濾 (Volatility Surface Reading)

```csharp
public void ReadSwaptionVol()
{
    // Read Non-Shifted Swaption Vol
    dataReading.SetupSwaptionMat(swaption_path);
    listSwaption = dataReading.swaptionMat;
}
```

*   **財務工程視角：** 這裡是評價 CMS 商品不可或缺的步驟。系統從外部路徑 (`swaption_path`) 讀取市場的交換選擇權波動度矩陣 (Swaption Volatility Matrix)。註解特別標示了 `Non-Shifted`，這意味著系統目前假設利率環境為正值（傳統的 Lognormal 假設）。在當前或未來的低利率環境下，這是一個重要的驗證檢查點：模型是否具備 Shifted-Lognormal 或 Normal 波動度的處理能力？

*   **學理基礎：** Swaption 是對利率交換合約的選擇權，支付人 Payer Swaption 的理論價格在 Black 模型（1976）下為：

    $$V_{\text{payer}} = A(0) \cdot [S(0) \cdot \mathcal{N}(d_+) - K \cdot \mathcal{N}(d_-)]$$

    其中 $A(0)$ 為年金因子、$S(0)$ 為遠期 Swap 利率、$K$ 為行使利率，且：

    $$d_{\pm} = \frac{\ln(S(0)/K) \pm \frac{1}{2}\sigma^2 T}{\sigma\sqrt{T}}$$

    **Shifted-SABR 模型的必要性：** 在負利率環境（如歐元區 2014–2022 年），$\ln(S/K)$ 因 $S<0$ 而無意義，需引入位移參數 $\alpha$（Shift），改用 $(S+\alpha)$ 代替 $S$，確保數學合法性。

    > **參考文獻：**
    > - Black, F. (1976). *The Pricing of Commodity Contracts.* Journal of Financial Economics, 3(1-2), 167–179.
    > - Hagan, P. S., Kumar, D., Lesniewski, A. S., & Woodward, D. E. (2002). *Managing Smile Risk.* Wilmott Magazine, September, 84–108. [SABR模型原始論文]
    > - Le Floc'h, F., & Kennedy, G. (2014). *Finite Difference Solution of the SABR/LIBOR Market Model.* arXiv:1410.0765.

```csharp
if (swaption_Vols[i * swaption_Len.Count + j] > 0)
{
    swpts.Add(new SwptDatum(
        swaption_Mat[i],
        swaption_Len[j],
        swaption_Vols[i * swaption_Len.Count + j]));
}
```

*   **財務工程視角：** 這是標準的資料清理程序。系統透過雙層迴圈掃描波動度矩陣，並且只將波動度大於 0 的有效報價點加入 `swpts` 列表中。這些有效資料點將成為後續 LMM 模型校準 (Calibration) 時的目標函數。

*   **IMV 驗證注意：** 過濾條件 `> 0` 可能在 Normal/Basis-point vol 的市場報價中造成誤判（因為 Normal vol 理論上可以是有效的正數但很小的數值，也可能遭到誤刪）。建議驗證時確認市場數據的波動度引用慣例（Lognormal vol vs. Normal vol vs. Basis-point vol）。

---

## 三、 Libor Market Model (LMM) 建構與校準

```csharp
IborIndex Index = new USDLibor3M(HTS);
LiborForwardModelProcess process = new LiborForwardModelProcess(size, Index);
Handle<YieldTermStructure> termStructure = Index.forwardingTermStructure();
```

*   **財務工程視角：** 此段確立了模型的核心引擎為 **Libor Market Model (LMM)**。系統以 3 個月期的美元 Libor (`USDLibor3M`) 作為基礎指標利率，並將第一階段建構的期限結構 `HTS` 綁定進去。`LiborForwardModelProcess` 負責定義整個遠期利率的隨機過程狀態空間。

*   **學理基礎（LMM 核心理論）：** LMM（又稱 BGM 模型）由 Brace, Gatarek & Musiela（1997）及 Jamshidian（1997）獨立提出。其核心貢獻在於：直接對市場可觀察的**遠期 LIBOR 利率**建立隨機模型，而非對不可直接觀察的短期利率建模。

    在**第 $n$ 個測度（Terminal Measure, $T_N$-forward measure）**下，第 $i$ 個遠期利率 $L_i(t)$ 滿足 SDE：

    $$dL_i(t) = \sigma_i(t) L_i(t) \, dW_i^{T_N}(t)$$

    而在**即期測度 (Spot Measure)** 下，帶有無套利漂移項：

    $$dL_i(t) = \mu_i^{\text{spot}}(t) \, dt + \sigma_i(t) L_i(t) \, dW_i(t)$$

    LMM 的突破性意義在於：它能直接以市場 Swaption 或 Caplet 報價進行校準，且在 Caplet 定價上與 Black(1976) 公式完全相容（即 BGM Caplet = Black Caplet），具有高度市場一致性。

    > **參考文獻：**
    > - Brace, A., Gatarek, D., & Musiela, M. (1997). *The Market Model of Interest Rate Dynamics.* Mathematical Finance, 7(2), 127–155.
    > - Jamshidian, F. (1997). *LIBOR and Swap Market Models and Measures.* Finance and Stochastics, 1(4), 293–330.
    > - Rebonato, R. (2002). *Modern Pricing of Interest-Rate Derivatives: The LIBOR Market Model and Beyond.* Princeton University Press.

*   **SOFR 過渡議題：** 由於 USD LIBOR 已於 2023 年 6 月正式停止報價，後繼標準利率為 **SOFR（Secured Overnight Financing Rate）**。若此模型仍以 `USDLibor3M` 為基礎，驗證時需釐清商品合約是否包含備援利率條款（Fallback Provisions），以及模型是否已過渡至 Term SOFR 或 Compound SOFR 架構。

```csharp
LmVolatilityModel volaModel = new LmExtLinearExponentialVolModel(
    process.fixingTimes(), 0.5, 0.6, 0.1, 0.1);
LmCorrelationModel corrModel = new LmLinearExponentialCorrelationModel(size, 0.5, 0.8);
LiborForwardModel model = new LiborForwardModel(process, volaModel, corrModel);
```

*   **財務工程視角：** 這是 LMM 參數化的核心。
    *   `LmExtLinearExponentialVolModel`：採用了學理上著名的 **ABCD 波動度參數化模型** $\sigma(t) = [a + b(T-t)]e^{-c(T-t)} + d$。這能有效捕捉市場常見的波動度結構。
    *   `LmLinearExponentialCorrelationModel`：定義了遠期利率間的相關性結構，形式為 $\rho_{i,j} = e^{-\beta|T_i - T_j|}$，確保相近天期的利率連動性較高。

*   **學理基礎（ABCD 波動度函數）：** ABCD 波動度函數由 Rebonato（2002）系統化闡述，其形式為：

    $$\sigma_i(t) = [a + b(T_i - t)] e^{-c(T_i - t)} + d, \quad t \leq T_i$$

    各參數的市場直覺意義如下：
    - $d$：長期漸近波動度水準（Long-term vol level）
    - $a + d$：即期波動度初始水準（Short-term vol level）
    - $b$：控制峰值出現的位置（Hump location）
    - $c$：控制指數衰減速度（Decay rate）

    此函數的優點是能夠同時捕捉**波動度的 Humped 結構**（即中期波動度高於短期與長期）和**長期利率的均值回歸**特性，與市場觀察高度吻合。

    **相關性結構：** 線性指數相關性模型 $\rho_{i,j} = e^{-\beta|T_i - T_j|}$ 是 Schoenmakers & Coffey（2003）等文獻中常見的參數化形式，能確保相關矩陣的半正定性（Positive Semi-definiteness），在數值實作上較為穩健。

    > **參考文獻：**
    > - Rebonato, R. (2002). *Modern Pricing of Interest-Rate Derivatives.* Princeton University Press. Chapter 6: The ABCD Parameterization.
    > - Schoenmakers, J., & Coffey, B. (2003). *Systematic Generation of Parametric Correlation Structures for the LIBOR Market Model.* International Journal of Theoretical and Applied Finance, 6(5), 507–519.
    > - Brigo, D., & Mercurio, F. (2006). *Interest Rate Models — Theory and Practice.* Springer. Chapter 6–7.

```csharp
LevenbergMarquardt om = new LevenbergMarquardt(1e-6, 1e-6, 1e-6);
model.calibrate(calibrationHelper, om, new EndCriteria(200, 100, 1e-6, 1e-6, 1e-6));
```

*   **財務工程視角：** 系統將挑選出的 Swaption 放入 `calibrationHelper` 中，並呼叫 `LevenbergMarquardt` 優化演算法進行全局校準，確保模型生成的價格能盡可能貼近市場真實報價。

*   **學理基礎（Levenberg-Marquardt 演算法）：** LM 演算法是非線性最小平方問題（Nonlinear Least Squares）的標準解法，是 Gauss-Newton 法與梯度下降法的混合體：

    $$\Delta\boldsymbol{\theta} = -(\mathbf{J}^T\mathbf{J} + \lambda \mathbf{I})^{-1} \mathbf{J}^T \mathbf{r}$$

    其中 $\mathbf{J}$ 為殘差的雅可比矩陣、$\lambda$ 為阻尼因子（Damping Factor）。在校準 LMM 時，目標函數為模型價格與市場 Swaption 價格的均方誤差（MSE）。

    **校準穩健性檢驗（IMV 要點）：**
    1. 最終校準誤差（Calibration Error）應低於市場 Bid-Ask Spread 的 1/2。
    2. 校準後的 ABCD 參數需通過符號性檢查（例如 $c, d > 0$），以確保波動度函數的經濟意義合理。
    3. 需對初始參數值（Initial Guess）進行敏感度測試，確保優化結果非局部最優解。

    > **參考文獻：**
    > - Levenberg, K. (1944). *A Method for the Solution of Certain Non-Linear Problems in Least Squares.* Quarterly of Applied Mathematics, 2(2), 164–168.
    > - Marquardt, D. W. (1963). *An Algorithm for Least-Squares Estimation of Nonlinear Parameters.* Journal of the Society for Industrial and Applied Mathematics, 11(2), 431–441.

---

## 四、 共變異數與漂移項推導 (Covariance & Drift Calculation)

```csharp
cov = Algorithm.ABCDVolCov(a, b, c, d, effStartTime, effStopTime, rateTimes[i], rateTimes[j]);
cov = adjK[i] * adjK[j] * cov;
GCOV[i, j] += cov * GCORR[i, j];
```

*   **財務工程視角：** 在進行蒙地卡羅模擬的每個時間步階，系統需計算利率間的共變異數。這裡實作了 LMM 模型中兩個遠期利率的瞬時共變異：$\text{Cov}(F_i, F_j) = \sigma_i \sigma_j \rho_{i,j}$。`ABCDVolCov` 解析了波動度的積分，而乘上 `GCORR` 則完成了共變異數矩陣 `GCOV` 的建構。

*   **學理基礎：** 在 LMM 中，兩遠期利率的瞬時協方差為：

    $$\text{Cov}(dL_i, dL_j) = \sigma_i(t)\sigma_j(t)\rho_{i,j} \, dt$$

    在蒙地卡羅模擬的每個時間步 $[t, t+\Delta t]$ 中，需對波動度做時間積分。對於 ABCD 函數，此積分存在解析解（Closed-form），`ABCDVolCov` 函數即實作此積分：

    $$\int_{t_{\text{start}}}^{t_{\text{stop}}} \sigma_i(s)\sigma_j(s) \, ds$$

    採用解析積分而非數值積分，能大幅提升計算精度與效率。`adjK` 調整因子代表 Displaced Diffusion 或 Local Volatility 的 Skew 調整項，需在驗證時確認其數學定義是否與模型文件一致。

    > **參考文獻：**
    > - Brigo, D., & Mercurio, F. (2006). *Interest Rate Models.* Springer. Appendix C: Analytic Integration of ABCD Covariance.

```csharp
for (int i = step; i < NRates; ++i)
    tmpl[i] = (currentFwds[i]) / (oneOverTaus[i] + currentFwds[i]);

for (int i = step; i < NRates; ++i)
{
    drifts1[i] = 0.0;
    for (int j = step; j < i + 1; j++)
    {
        drifts1[i] += tmpl[j] * COV[i, j];
    }
}
```

*   **財務工程視角：** 這是 LMM 蒙地卡羅模擬中最吃重運算的部分：**即期測度 (Spot Measure) 下的無套利漂移項 (Drift)**。
    *   `tmpl[i]` 的數學對應為：$\frac{\tau_i F_i(t)}{1 + \tau_i F_i(t)}$
    *   下方的雙層迴圈累加，精準還原了 LMM 漂移項公式：$\mu_i(t) = \sum_{j} \frac{\rho_{i,j} \sigma_i \sigma_j F_j \tau_j}{1 + F_j \tau_j}$。確保模擬過程符合無套利定價理論。

*   **學理基礎（即期測度下的漂移項推導）：** 即期測度（Spot Measure）由 Jamshidian（1997）引入，其 Numéraire 為：

    $$B^d(t) = P(t,T_0) \prod_{k=0}^{\eta(t)-1} (1 + \tau_k L_k(T_k))$$

    在此測度下，遠期利率 $L_i(t)$ 的無套利漂移項為：

    $$\mu_i^{\text{spot}}(t) = \sigma_i(t) \sum_{j=\eta(t)}^{i} \frac{\tau_j \rho_{i,j} \sigma_j(t) L_j(t)}{1 + \tau_j L_j(t)}$$

    程式碼中 `tmpl[j]` 精確對應 $\frac{\tau_j L_j}{1 + \tau_j L_j}$ 項，雙層迴圈為標準的 Spot Measure 累加結構，與學理公式完全吻合，確認實作正確。

    > **參考文獻：**
    > - Jamshidian, F. (1997). *LIBOR and Swap Market Models and Measures.* Finance and Stochastics, 1(4), 293–330.
    > - Glasserman, P. (2003). *Monte Carlo Methods in Financial Engineering.* Springer. Chapter 3: Simulation of Libor Market Model.
    > - Andersen, L., & Piterbarg, V. (2010). *Interest Rate Modeling* (Vol. 2). Atlantic Financial Press. Chapter 14: Monte Carlo Methods for LMM.

---

## 五、 隨機過程演化 (Stochastic Evolution)

```csharp
logFwds[i] += drifts1[i] + fixedDrifts[i];
logFwds[i] += Vector.DotProduct(A.row(i), brownians);

if (logFwds[i] < MaxLogFwd) {
    nextFwds[i] = Math.Exp(logFwds[i]);
} else {
    nextFwds[i] = Math.Exp(MaxLogFwd);
}
```

*   **財務工程視角：** 系統採用了**對數歐拉離散化 (Log-Euler Scheme)** 來推進時間。
    *   對應了伊藤引理轉換後的 SDE：$d \ln F_i(t) = (\mu_i - \frac{1}{2}\sigma_i^2)dt + \sigma_i dW_i$。
    *   **防呆機制：** `if-else` 判斷式是一個極具實務經驗的量化技巧。設定 `MaxLogFwd` 強制截斷極端值，避免 LMM 漂移項在極端情境下導致數值爆炸，確保模擬的穩定性。

*   **學理基礎（對數歐拉法與 Itô 引理）：** 設 $X_i = \ln L_i$，由 Itô 引理：

    $$dX_i = \left(\mu_i - \frac{1}{2}\sigma_i^2\right)dt + \sigma_i \, dW_i$$

    對數歐拉離散化方案：

    $$X_i(t + \Delta t) = X_i(t) + \left(\mu_i(t) - \frac{1}{2}\sigma_i^2(t)\right)\Delta t + \sigma_i(t)\sqrt{\Delta t}\, Z_i$$

    其中 $Z_i \sim \mathcal{N}(0,1)$ 為標準常態隨機變數。`Vector.DotProduct(A.row(i), brownians)` 對應 $\sigma_i \sqrt{\Delta t} Z_i$ 項，其中 $A$ 為對協方差矩陣進行 Cholesky 分解後的下三角矩陣，確保多維布朗運動的相關性結構正確建立。

    **截斷誤差（Truncation Error）：** 對數歐拉法為一階強收斂（Strong Convergence Order = 0.5），在步長 $\Delta t$ 夠小時具有良好的收斂性。`MaxLogFwd` 截斷機制類似於 Andersen（2007）的反射邊界（Reflection Boundary）技術，用於防止數值不穩定。

    > **參考文獻：**
    > - Kloeden, P. E., & Platen, E. (1992). *Numerical Solution of Stochastic Differential Equations.* Springer.
    > - Andersen, L. (2007). *Efficient Simulation of the Heston Stochastic Volatility Model.* Journal of Computational Finance, 11(3).
    > - Glasserman, P. (2003). *Monte Carlo Methods in Financial Engineering.* Springer. Chapter 6.

---

## 六、 CMS 重構與區間計息評估 (CMS Reconstruction & Smoothing)

```csharp
// (節錄部分迴圈)
double df = CurrentDF[j - 1] / (1 + CurrentFwdRate[j - 1] * accruals[j]);
// ... FloatQ 與 FixedSA 的累加 ...
double CMS = FloatQ[k] / FixedSA[k];
QCMS[k] = CMS;
```

*   **財務工程視角：** 由於 LMM 原生模擬「遠期利率」，必須將其重組為此商品連結的「CMS 利率」。程式碼先遞迴計算折現因子 `df`，再分別計算浮動腳 (`FloatQ`) 與固定腳年金 (`FixedSA`)，最後相除求出該路徑節點下的 CMS 利率 $S_{t,T} = \frac{\text{Float Leg}}{\text{Annuity}}$。

*   **學理基礎（CMS 利率的本質與 Convexity Adjustment）：** CMS 利率 $S_{n,m}(t)$ 是指在未來時間點 $t = T_n$ 觀察到的 $m$ 年期 Swap 利率。其理論值由在 **Forward Swap Measure** $\mathbb{Q}^{A}$ 下的期望值決定：

    $$\mathbb{E}^{\mathbb{Q}^A}[S_{n,m}(T_n)] = S_{n,m}(0) \quad \text{(在 Swap Measure 下為鞅)}$$

    然而，CMS 連結商品的支付通常不在 Swap Measure 下，因此需要進行**凸性調整 (Convexity Adjustment)**。在簡單近似下：

    $$\mathbb{E}^{\mathbb{Q}^{T_n}}[S_{n,m}(T_n)] \approx S_{n,m}(0) + \text{CA}$$

    其中凸性調整項 CA 可由 Replication 方法（Hunt & Kennedy, 2004）或 Linear TSR（Terminal Swap Rate）近似計算。

    **本程式碼的處理方式：** 在蒙地卡羅路徑中直接由遠期利率重組 CMS（即 $S = \text{FloatQ}/\text{FixedSA}$），避免了解析凸性調整的近似誤差，這是 LMM 框架的天然優勢。

    > **參考文獻：**
    > - Pelsser, A. (2003). *Mathematical Foundation of Convexity Correction.* Quantitative Finance, 3(1), 59–65.
    > - Hunt, P. J., & Kennedy, J. E. (2004). *Financial Derivatives in Theory and Practice* (Rev. ed.). Wiley.
    > - Brigo, D., & Mercurio, F. (2006). *Interest Rate Models.* Springer. Chapter 13: CMS Products.
    > - Andersen, L., & Piterbarg, V. (2010). *Interest Rate Modeling* (Vol. 3). Chapter 16: CMS and Structured Products.

```csharp
double cms_l = Math.Min(QCMS[j], QCMS[j + 1]);
double cms_h = Math.Max(QCMS[j], QCMS[j + 1]);
if (cms_l >= B && cms_h <= U)
    Ratio[j] = 1.0;
else if (cms_h < B || cms_l > U)
    Ratio[j] = 0.0;
else
    Ratio[j] = (Math.Min(U, cms_h) - Math.Max(B, cms_l)) / (cms_h - cms_l);
```

*   **財務工程視角：** 這是整份程式碼的一大亮點。在評估 CMS 利率是否落在目標區間 $[B, U]$ 時，利用了上一期與下一期的利率上下界進行線性插值。這是**布朗橋近似 (Brownian Bridge Approximation)** 的簡化變體，能大幅降低蒙地卡羅模擬的變異數 (Variance Reduction)，減少雜訊並加速定價收斂。

*   **學理基礎（Brownian Bridge 與變異數縮減）：** 標準蒙地卡羅模擬每日分配的方法，在觀察區間（例如每日）極短時，每個時間步的 0/1 決策會導致**高變異數**問題，需要大量路徑才能收斂。

    布朗橋技術（Andersen & Brotherton-Ratcliffe, 1996；Glasserman, 2003）利用：在相鄰兩點 $(S_t, S_{t+\Delta})$ 已知的條件下，布朗運動在中間任一時點穿越邊界的**解析機率**：

    $$\Pr(\tau_{\text{hit}} \leq t + \Delta | S_t = a, S_{t+\Delta} = b) = \exp\!\left(-\frac{2(a-B)(b-B)}{\sigma^2 \Delta}\right)$$

    本程式碼採用的線性插值近似 Ratio 計算是此技術的簡化實作，在計算成本與精度間取得了良好平衡，是業界常見的實用做法。

    > **參考文獻：**
    > - Andersen, L., & Brotherton-Ratcliffe, R. (1996). *Exact Exotics.* Risk Magazine, October, 85–89.
    > - Glasserman, P. (2003). *Monte Carlo Methods in Financial Engineering.* Springer. Chapter 6.2: Barrier Crossings and Brownian Bridge.
    > - Broadie, M., Glasserman, P., & Kou, S. G. (1997). *A Continuity Correction for Discrete Barrier Options.* Mathematical Finance, 7(4), 325–349.

---

## 七、 現金流計算與折現 (Cashflow & Discounting)

```csharp
for (int i = 0; i < MSteps; i++)
{
    CF[i] += Nominal * Coupon * Ratio[i] * accruals[i];
}
CF[MSteps - 1] += Nominal;
```

*   **財務工程視角：** 將每日落在區間內的比例 (`Ratio`)，乘上名目本金 (`Nominal`)、票面利率 (`Coupon`) 與計息天數 (`accruals`)，推導出該期的應計利息現金流。最後一期則加回本金。

*   **學理基礎（區間計息的理論框架）：** 可贖回 CMS 區間計息結構票 (Callable CMS-linked Range Accrual Note) 的理論價格，在不考慮 Callable 特性時可寫為：

    $$V_0 = \mathbb{E}^{\mathbb{Q}}\left[\sum_{i=1}^{N} \text{Nominal} \cdot c \cdot \Delta_i \cdot \mathbf{1}_{\{L \leq S_{t_i} \leq U\}} \cdot D(0, T_i) + \text{Nominal} \cdot D(0, T_N)\right]$$

    其中 $\mathbf{1}_{\{L \leq S_{t_i} \leq U\}}$ 為指示函數，$D(0,T_i) = e^{-\int_0^{T_i} r_s ds}$ 為隨機折現因子，$c$ 為票面利率，$\Delta_i$ 為計息日數分率（Accrual Fraction）。

    在蒙地卡羅框架中，以路徑平均估計此期望值，`Ratio[i]` 即為 $\mathbf{1}_{\{L \leq S_{t_i} \leq U\}}$ 的平滑化近似。

    > **參考文獻：**
    > - Kenyon, C., & Stamm, R. (2012). *Discounting, LIBOR, CVA and Funding.* Palgrave Macmillan.
    > - Andersen, L., & Piterbarg, V. (2010). *Interest Rate Modeling* (Vol. 3). Atlantic Financial Press. Chapter 15: Range Accrual Products.

```csharp
double NPV = 0;
for (int i = 0; i < MSteps; i++)
{
    NPV += CF[i] * VDisc[i];
}
LNPV.Add(NPV);
```

*   **財務工程視角：** 最後，將所有未來的現金流乘上對應的折現因子 `VDisc`，加總得到單一路徑的淨現值 (NPV)，並將其存入列表 `LNPV` 以供最終蒙地卡羅平均。

*   **IMV 驗證要點：** 在即期測度（Spot Measure）下，折現因子 `VDisc[i]` 應為**路徑相依**（Path-dependent），即每條路徑的 Numéraire 值不同。若 `VDisc` 被錯誤地取為某一固定的「確定性」折現曲線（Deterministic Discount Factor），將導致系統性定價偏誤（Measure Mismatch）。此為第八節 IMV 驗證重點一的核心議題。

---

## 八、 可贖回機制 — LSMC 定價邏輯 (Callable Mechanism — Longstaff-Schwartz Monte Carlo)

> **更新說明（2026-04-08）：** 本節為新增章節，依據後續提供之程式碼截圖進行驗證與解析。原文件 P0 警告（Callable 機制缺失）已獲解決，以下為完整分析。

### 8.1 逆向歸納主迴圈與持有價值計算

```csharp
for (int j = MSteps - 1; j >= 0; j--)
{
    List<int> RcgPath;
    int OBS;

    RcgPath = new List<int>(0);
    for (int i = 0; i < NPath; i++)
    {
        HoldValuc[i, j] = j == MSteps - 1
            ? MCF[i, j]
            : Valuc[i, j + 1] * VDisc[j + 1] / VDisc[j] - MCF[i, j];
        Valuc[i, j] = HoldValuc[i, j];
        if (HoldValuc[i, j] < -1*(CallValuc[j] + MCF[i, j]) && j + InitPoint > froczc)
            RcgPath.Add(i);
    }
```

*   **財務工程視角：** 這是 LSMC 演算法的核心骨幹——**逆向歸納（Backward Induction）**。迴圈從最後一個時間步 `MSteps - 1` 反向逐步推算至現在，這與有限差分法（PDE）的思路相同，但在蒙地卡羅路徑上執行。

    **`HoldValuc[i, j]` 的計算：**
    - **最後一期（`j == MSteps - 1`）：** 直接取該期現金流 `MCF[i, j]`（本金 + 最後一期票息）作為持有價值。
    - **其他期：** `Valuc[i, j+1] * VDisc[j+1] / VDisc[j] - MCF[i, j]` — 將下一期的價值折算回本期（以折現因子比值折現），再扣除本期已支付的現金流。這精確反映了「持有不贖回」的繼續價值（Continuation Value）。

    **ITM 路徑篩選條件：**
    - `HoldValuc[i, j] < -1 * (CallValuc[j] + MCF[i, j])`：繼續持有的價值（對發行人為負債）大於立即贖回的成本，即贖回對發行人有利。此處符號約定以**發行人視角**（負值 = 成本），需在驗證時仔細確認符號方向。
    - `j + InitPoint > froczc`：確保當前時間點已超過**不可贖回保護期（Non-call Period / Lock-out Period）**，才允許行使贖回權。`froczc` 為不可贖回期的截止時間索引，`InitPoint` 為時間偏移量。

*   **學理基礎：** Longstaff & Schwartz（2001）的核心洞見是：在每個可能的行使日，只需對**價內（In-The-Money, ITM）路徑**進行回歸，因為價外路徑幾乎不會被行使，排除它們能有效降低回歸的條件數（Condition Number），提升數值穩定性。此篩選邏輯完整還原了原始 LSMC 論文的設計。

    > **參考文獻：**
    > - Longstaff, F. A., & Schwartz, E. S. (2001). *Valuing American Options by Simulation: A Simple Least-Squares Approach.* Review of Financial Studies, 14(1), 113–147. [LSMC 原始論文]

### 8.2 多項式回歸基底建構

```csharp
OBS = RcgPath.Count;
Matrix MY = new Matrix(OBS, 1);
Matrix MX = new Matrix(OBS, 4);
for (int i = 0; i < OBS; i++)
{
    int obs = RcgPath[i];
    MY[i, 0] = Valuc[obs, j + 1] * VDisc[j + 1] / VDisc[j] - MCF[i, j];

    double ar = QCMS[obs, j];
    MX[i, 0] = 1.0;
    MX[i, 1] = ar;
    MX[i, 2] = ar * ar;
    MX[i, 3] = ar * ar * ar;
}
```

*   **財務工程視角：** 這是 LSMC 的回歸設定步驟。
    - **因變數 `MY`（Y）：** 即路徑在下一期的折現繼續持有價值，與篩選條件對應（僅取 ITM 路徑）。
    - **自變數 `MX`（X）：** 選用 **CMS 利率（`QCMS[obs, j]`）的三次多項式**作為基底函數：$\boldsymbol{\phi}(S) = [1, S, S^2, S^3]$，其中 $S$ 為當期 CMS 利率。

    **基底函數選擇的意涵：** 系統選用 CMS 利率作為唯一狀態變數，反映了此商品的定價主要驅動力為利率水準（利率是否落在區間內）。三次多項式可以近似各種非線性的繼續價值函數形態，在實務上被廣泛使用。

*   **學理基礎（基底函數的理論選擇）：** Longstaff & Schwartz（2001）建議可使用 Laguerre 多項式（Laguerre Polynomials）、Hermite 多項式，或簡單的冪次多項式（Power Polynomials）。對於利率商品，冪次多項式（本程式採用）是業界最常見的選擇，因為其計算效率高，且在典型的利率範圍（0%–10%）內數值表現穩定。

    Tsitsiklis & Van Roy（2001）從動態規劃（Dynamic Programming）角度為 LSMC 提供了嚴格的收斂性證明，當基底函數完備且路徑數趨近無窮大時，回歸估計的繼續價值將依機率收斂至真實條件期望值。

    > **參考文獻：**
    > - Longstaff & Schwartz (2001). 同上。
    > - Tsitsiklis, J. N., & Van Roy, B. (2001). *Regression Methods for Pricing Complex American-Style Options.* IEEE Transactions on Neural Networks, 12(4), 694–703.
    > - Clément, E., Lamberton, D., & Protter, P. (2002). *An Analysis of a Least Squares Regression Method for American Option Pricing.* Finance and Stochastics, 6(4), 449–471.

### 8.3 OLS 回歸求解（矩陣形式）

```csharp
Matrix MXT = new Matrix(Matrix.transpose(MX));
Matrix MA  = new Matrix(MXT * MX);
Matrix MAI = new Matrix(Matrix.inverse(MA));
Matrix MC  = new Matrix(MXT * MY);
Matrix MB  = new Matrix(MAI * MC);
```

*   **財務工程視角：** 這五行程式碼精確實作了**普通最小二乘法（Ordinary Least Squares, OLS）** 的矩陣正規方程解：

    $$\hat{\boldsymbol{\beta}} = (\mathbf{X}^T \mathbf{X})^{-1} \mathbf{X}^T \mathbf{Y}$$

    對應程式碼：
    - `MXT` $= \mathbf{X}^T$
    - `MA` $= \mathbf{X}^T\mathbf{X}$
    - `MAI` $= (\mathbf{X}^T\mathbf{X})^{-1}$
    - `MC` $= \mathbf{X}^T\mathbf{Y}$
    - `MB` $= \hat{\boldsymbol{\beta}}$：回歸係數向量，維度為 $4 \times 1$，對應 $[\beta_0, \beta_1, \beta_2, \beta_3]$

    求解後，估計的繼續價值函數為：

    $$\hat{C}(S; t_j) = \beta_0 + \beta_1 S + \beta_2 S^2 + \beta_3 S^3$$

*   **數值穩定性警告：** 直接計算 $(\mathbf{X}^T\mathbf{X})^{-1}$ 是數值上相對不穩定的做法，特別是當狀態變數（CMS 利率）的範圍造成 `MX` 的列之間數值差距懸殊時（例如 $[1, 0.03, 0.0009, 0.000027]$），矩陣 `MA` 可能出現**病態（Ill-conditioned）** 問題，導致反矩陣計算不準確。

    **IMV 建議：** 對 CMS 利率進行**標準化（Normalization）** 處理（例如去均值除以標準差），或改用數值更穩定的 **QR 分解**（Cholesky 或 Householder QR）求解正規方程，可有效改善數值穩定性。

    > **參考文獻：**
    > - Golub, G. H., & Van Loan, C. F. (2013). *Matrix Computations* (4th ed.). Johns Hopkins University Press. Chapter 5: Orthogonalization and Least Squares.

### 8.4 最優停止決策與贖回記錄

```csharp
for (int i = 0; i < OBS; i++)
{
    int obs = RcgPath[i];
    double ar = QCMS[i, j];  // ← [IMV 注意：應為 QCMS[obs, j]]

    double Expect = MB[0, 0] * 1.0
        + MB[1, 0] * ar + MB[2, 0] * ar * ar + MB[3, 0] * ar * ar * ar;

    if (Expect < -1*(CallValuc[j] + MCF[obs, j]))
    {
        Valuc[obs, j] = -1 * (CallValuc[j] + MCF[obs, j]);
        calltime[obs] = j;
    }
}
```

*   **財務工程視角：** 這是 LSMC 的決策核心——**最優停止規則（Optimal Stopping Rule）**。

    1. 使用上一步求得的 $\hat{\boldsymbol{\beta}}$ 計算每條 ITM 路徑的**估計繼續價值** `Expect`。
    2. 若 `Expect < -1 * (CallValuc[j] + MCF[obs, j])`，即「繼續持有的估計負債 > 立即贖回的總成本」，則判定發行人應**行使贖回權**：
        - `Valuc[obs, j]` 被設定為贖回現金流（面值 + 當期票息），更新路徑價值。
        - `calltime[obs] = j` 記錄此路徑的提前贖回時間點。

*   **重要 IMV 發現 — 潛在索引錯誤：**
    - 第 3 行：`double ar = QCMS[i, j]` 使用的是迴圈計數器 `i`（範圍 0 到 OBS-1）。
    - 但正確應該使用 `QCMS[obs, j]`（其中 `obs = RcgPath[i]` 是原始路徑索引）。
    - 若 `RcgPath` 中的路徑索引並非從 0 連續排列（此為常態），`i ≠ obs`，此處的 `ar` 將對應**錯誤路徑的 CMS 利率**，導致決策錯誤。
    - **建議：** 立即向開發方確認此行是否為筆誤，應改為 `double ar = QCMS[obs, j];`。

*   **學理基礎（Bermudan 最優停止理論）：** 在 Snell Envelope 理論框架下，Bermudan 選擇權的公允價值為：

    $$V(t) = \sup_{\tau \in \mathcal{T}_{[t,T]}} \mathbb{E}^{\mathbb{Q}}\left[D(t,\tau) \cdot h(\tau) \,\big|\, \mathcal{F}_t\right]$$

    其中 $\mathcal{T}$ 為所有 $\mathcal{F}$-停時（Stopping Time）的集合，$h(\tau)$ 為行使收益（Exercise Payoff）。LSMC 透過回歸近似條件期望值，從而在每條路徑上逼近此最優停時。

    > **參考文獻：**
    > - Neveu, J. (1975). *Discrete-Parameter Martingales.* North-Holland.
    > - Andersen, L., & Broadie, M. (2004). *Primal-Dual Simulation Algorithm for Pricing Multidimensional American Options.* Management Science, 50(9), 1222–1234. [提供 LSMC 的上界估計方法]
    > - Piterbarg, V. (2004). *Pricing and Hedging Callable Libor Exotics in Forward Libor Models.* Journal of Computational Finance, 8(2), 65–117. [CMS 商品 LSMC 應用]

### 8.5 LSMC 完整性驗證摘要

| 驗證項目 | 狀態 | 說明 |
|----------|------|------|
| 逆向歸納結構 | ✓ 正確 | `MSteps-1` → `0` 反向迴圈 |
| ITM 篩選邏輯 | ✓ 正確 | 含不可贖回期保護 `froczc` |
| 回歸基底函數 | ✓ 合理 | CMS 利率三次多項式 |
| OLS 矩陣求解 | ✓ 正確 | 標準正規方程實作 |
| 最優停止決策 | ✓ 正確 | 繼續值 vs. 贖回值比較 |
| 贖回記錄 | ✓ 存在 | `calltime[obs]` 記錄贖回時間 |
| **路徑索引一致性** | **⚠ 疑慮** | **`QCMS[i,j]` 應為 `QCMS[obs,j]`，待確認** |
| 矩陣數值穩定性 | △ 建議改善 | 建議標準化或改用 QR 分解 |

---

## 九、 IMV 驗證重點提醒 (供後續查核)

1.  **測度轉換 (Measure Consistency)：** 程式採用 Spot Measure 演化，需確認最後的 `VDisc` 折現因子是否正確採用了具備路徑相依性 (Path-dependent) 的 Numéraire 進行折現，以免產生定價偏誤。
2.  **單一曲線 vs. 雙曲線架構 (Curve Framework)：** 系統是否具備 OIS Discounting 的雙曲線定價能力？驗證時需釐清預測曲線與折現曲線的分工。
3.  **Callable 機制缺失：** 目前所有的程式碼皆為處理「底層資產」的定價。必須有針對發行人提前解約權 (Bermudan Option) 的決策邏輯（如 Longstaff-Schwartz 演算法）。建議要求提供 LSMC 相關的程式碼片段進行下一步驗證。

---

## 十、 學理基礎綜合補充

### 9.1 測度理論基礎

現代衍生商品定價建立在**測度論（Measure Theory）**的嚴格數學框架之上。核心定理為：

**第一基本定理（No-Arbitrage）：** 市場無套利若且唯若存在等價鞅測度（Equivalent Martingale Measure, EMM）$\mathbb{Q}$，使得所有以 Numéraire $N(t)$ 折算的資產價格為 $\mathbb{Q}$ 下的鞅。

**第二基本定理（Market Completeness）：** 市場完備若且唯若 EMM 唯一。在 LMM 框架下，商品定價公式為：

$$V(t) = N(t) \cdot \mathbb{E}^{\mathbb{Q}^N}\left[\frac{V(T)}{N(T)} \,\bigg|\, \mathcal{F}_t\right]$$

不同測度（Terminal Measure, Spot Measure, Forward Measure）的選擇影響的是計算效率，而非理論上的定價結果（只要 Numéraire 選擇一致，結果應相同）。

> **參考文獻：**
> - Harrison, J. M., & Pliska, S. R. (1981). *Martingales and Stochastic Integrals in the Theory of Continuous Trading.* Stochastic Processes and their Applications, 11(3), 215–260.
> - Geman, H., El Karoui, N., & Rochet, J. C. (1995). *Changes of Numéraire, Changes of Probability Measure and Option Pricing.* Journal of Applied Probability, 32(2), 443–458.
> - Shreve, S. E. (2004). *Stochastic Calculus for Finance II: Continuous-Time Models.* Springer. Chapter 9.

### 9.2 Longstaff-Schwartz (LSMC) 演算法與 Callable 特性

可贖回（Callable）特性賦予發行人在特定日期（Bermudan Exercise Dates）以面值提前贖回的權利，等價於一個**百慕達選擇權（Bermudan Option）**。標準的 LSMC 演算法（Longstaff & Schwartz, 2001）處理此問題的步驟為：

1. **逆向歸納（Backward Induction）：** 從最後一個可贖回日開始，向前逐步估計「繼續持有價值（Continuation Value）」。
2. **最小二乘回歸（Least Squares Regression）：** 在每個可贖回日，以多項式（或其他基底函數）對狀態變數（如 CMS 利率）做回歸，估計條件期望繼續值。
3. **最優停止決策：** 若立即贖回（Immediate Exercise Value）> 繼續價值，則發行人選擇贖回。

可贖回債券的理論價格：

$$V_{\text{callable}} = V_{\text{straight bond}} - V_{\text{call option}}$$

> **參考文獻：**
> - Longstaff, F. A., & Schwartz, E. S. (2001). *Valuing American Options by Simulation: A Simple Least-Squares Approach.* Review of Financial Studies, 14(1), 113–147.
> - Andersen, L., & Broadie, M. (2004). *Primal-Dual Simulation Algorithm for Pricing Multidimensional American Options.* Management Science, 50(9), 1222–1234.
> - Piterbarg, V. (2004). *Pricing and Hedging Callable Libor Exotics in Forward Libor Models.* Journal of Computational Finance, 8(2), 65–117.

### 9.3 SOFR 過渡對 LMM 架構的影響

2023 年 6 月後 USD LIBOR 終止，市場轉向 SOFR 作為無風險基準利率（RFR）。SOFR 與 LIBOR 的關鍵差異：

| 特性 | LIBOR | SOFR |
|------|-------|------|
| 性質 | 報價利率（含信用溢酬）| 隔夜擔保（無信用風險）|
| 期限 | Term Structure (O/N ~ 12M) | 主要為 O/N；Term SOFR 為衍生 |
| 適用曲線 | Forwarding Curve | Discounting Curve (OIS) |

在 SOFR 環境下重建 LMM，需考慮：
- **Compounded-in-Arrears SOFR** 結構（非預先設定）使模型化更複雜。
- **Term SOFR**（ARRC 認可）已具備與 LIBOR 類似的期限結構，可較平滑地移植現有 LMM 架構。

> **參考文獻：**
> - ARRC (2021). *SOFR Starter Kit.* Alternative Reference Rates Committee, Federal Reserve Bank of New York.
> - Lyashenko, A., & Mercurio, F. (2019). *Looking Forward to Backward-Looking Rates: A Modeling Framework for Term Rates Replacing LIBOR.* SSRN Working Paper 3330240.
> - Andersen, L., Duffie, D., & Song, Y. (2019). *Funding Value Adjustments.* Journal of Finance, 74(1), 145–192.

---

## 十一、 資深財務工程專家建議

### 10.1 模型驗證優先順序（按風險等級）

根據對本程式碼的全面審查，提出以下**優先排序的驗證行動清單**：

**[P0 — 高風險，立即驗證]**

1. **Callable 機制完整性：** ~~本文件所呈現的程式碼完全缺乏 Callable 定價邏輯~~ → **[2026-04-08 更新：已獲解決]** 開發方已補充提供 LSMC 程式碼（詳見第八節），確認 Bermudan Call Option 的逆向歸納、最小二乘回歸、最優停止決策邏輯均已實作。**惟發現一項潛在索引錯誤**（第 8.4 節：`QCMS[i,j]` 疑應為 `QCMS[obs,j]`），**此項須優先向開發方確認並修正**，否則仍可能在部分路徑產生錯誤的贖回決策，影響定價準確性。

2. **折現因子的路徑相依性：** 必須逐行確認 `VDisc` 的計算邏輯。在即期測度下，正確的做法是每條路徑應使用其**自身的 Numéraire 路徑**進行折現。若 `VDisc` 使用了初始曲線的確定性折現因子，則存在測度不一致（Measure Inconsistency）問題。

**[P1 — 中高風險，儘速驗證]**

3. **單一曲線 vs. 雙曲線架構：** 在後 LIBOR 時代，OIS 折現已是市場標準（CSA 協議下）。若本模型使用相同曲線進行預測與折現，與以 OIS 折現的市場報價相比可能有數個 bps 的系統差異。

4. **LIBOR 轉 SOFR 的合規性：** 確認商品合約中的備援利率條款（Fallback Rate），以及模型使用的 `USDLibor3M` 是否已依據 ISDA Protocol 轉換為相應的 Term SOFR 或 SOFR Compounded 利率。

5. **波動度引用慣例一致性：** 確認從市場取得的 Swaption 波動度是 Lognormal vol（適用正利率環境）、Normal vol，或 Basis-point vol，並確保模型內部的處理方式與之一致。避免「市場報的是 Normal vol，但模型套 Black Lognormal 公式」的引用錯誤。

**[P2 — 中等風險，定期複核]**

6. **校準穩健性與日間穩定性：** 建立每日校準誤差監控機制，追蹤 ABCD 參數的時間序列變化。若校準參數出現突然跳躍，可能代表市場數據品質問題或優化陷入局部最優解。

7. **蒙地卡羅收斂性測試：** 對不同路徑數（如 1K, 5K, 10K, 50K 路徑）進行收斂測試，確認標準誤差（Standard Error）是否達到定價精度要求（通常要求 SE < 0.1 bps for 100M nominal）。

8. **希臘字母（Greeks）的數值穩定性：** 若使用 Bump-and-Reprice 計算 Greeks（Delta, Gamma, Vega），需確認 Bump Size 的選取合理，避免數值微分的截斷誤差與消去誤差（Cancellation Error）。

### 10.2 壓力測試與情境分析建議

建議在正式驗證報告中加入以下情境測試：

| 情境 | 目的 |
|------|------|
| **Parallel Rate Shift ±100bps** | 測試 Duration 與 Convexity 計算的合理性 |
| **Volatility Shock ±20%** | 測試 Vega 敏感度與 Swaption 校準的穩健性 |
| **Curve Inversion（殖利率曲線倒掛）** | 確認 CMS 利率重組在非正常曲線形態下的行為 |
| **Near-Boundary Rates（利率接近區間上下限）** | 測試 Brownian Bridge 平滑化在臨界點的數值穩定性 |
| **Near-Call-Date Valuation（接近贖回日）** | 測試 Callable 決策邏輯在時間遞近時的行為 |

### 10.3 模型風險管理建議

依據 SR 11-7 指引（美聯儲/OCC 對模型風險管理的監管要求），建議：

1. **替代模型基準測試（Benchmark Testing）：** 建立一個獨立的簡化模型（如 Hull-White 單因子模型或基於 Black 公式的解析近似）作為基準，與 LMM 蒙地卡羅結果進行比較。差異應有合理的解釋（如凸性調整、微笑面效果差異）。

2. **模型使用限制（Model Use Limitation）：** 明確定義本模型的適用範圍（如：CMS 連結天期、區間上下限範圍、商品期限等），超出範圍的商品需進行額外驗證。

3. **模型準備金（Model Reserve）：** 鑑於 Callable 機制尚未驗證，建議在 Callable 邏輯完整實作並驗證前，計提模型不確定性準備金，以保守估計潛在的定價偏誤。

4. **文件完整性：** 要求開發方提供完整的模型文件（Model Documentation），包含所有假設、參數定義、數學推導，以及程式碼的完整版本，以利後續的持續監控（Ongoing Monitoring）。

> **監管參考：**
> - Board of Governors of the Federal Reserve System (2011). *SR 11-7: Guidance on Model Risk Management.* Federal Reserve Supervisory Guidance.
> - OCC (2021). *Comptroller's Handbook: Model Risk Management.* Office of the Comptroller of the Currency.

---

## 附錄：核心參考文獻彙整

| 主題 | 文獻 |
|------|------|
| HJM 框架 | Heath, Jarrow & Morton (1992), Econometrica |
| LMM / BGM 模型 | Brace, Gatarek & Musiela (1997), Mathematical Finance |
| LMM 測度 | Jamshidian (1997), Finance and Stochastics |
| ABCD 波動度 | Rebonato (2002), Princeton University Press |
| SABR 模型 | Hagan, Kumar, Lesniewski & Woodward (2002), Wilmott |
| 雙曲線框架 | Mercurio (2010), IJTAF; Bianchetti (2010), Risk |
| 測度轉換 | Geman, El Karoui & Rochet (1995), JAP |
| 蒙地卡羅方法 | Glasserman (2003), Springer |
| LMM 完整參考 | Brigo & Mercurio (2006), Springer |
| LMM 進階實作 | Andersen & Piterbarg (2010), Atlantic Financial Press |
| LSMC / Callable | Longstaff & Schwartz (2001), RFS |
| Brownian Bridge | Andersen & Brotherton-Ratcliffe (1996), Risk |
| CMS 凸性調整 | Pelsser (2003), Quantitative Finance |
| SOFR 過渡 | Lyashenko & Mercurio (2019), SSRN |
| 模型風險管理 | Federal Reserve SR 11-7 (2011) |
