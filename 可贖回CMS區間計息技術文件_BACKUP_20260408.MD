# 【獨立模型驗證技術手冊】可贖回 CMS 連結區間計息定價模型
**Model Validation Technical Document: Callable CMS-linked Daily Range Accrual**

## 一、 評價基準日與基礎利率曲線建構 (Valuation Date & Term Structure Setup)

```csharp
public void SetupTermStructure()
{
    Settings.setEvaluationDate(Today);
```

*   **財務工程視角：** 這是所有量化定價引擎（特別是類似 QuantLib 的開源庫或其衍生架構）的起手式。這行程式碼設定了**全域評價基準日 (Valuation Date)**。在定價模型中，這個日期代表時間原點 $t=0$。未來所有的現金流折現因子 (Discount Factor) $P(t,T)$ 以及所有距離到期日的時間 $\tau = T - t$ 計算，都會以此日期為基準。在驗證上，必須確保系統帶入的 `Today` 與實際的市場數據日期（通常是 T-1 日或 T 日的 EOD 數據）完全對齊。

```csharp
    //---------------------- Create Original Curve ----------------------
    dataReading = new DataReading();
    dataReading.SetupTermStructure(Today, swap_path);
```

*   **財務工程視角：** 這裡實例化了一個讀取數據的物件，並呼叫 `SetupTermStructure` 載入市場報價數據。從變數名稱 `swap_path` 可以明確看出，模型正在從外部檔案或資料庫讀取**利率交換報價 (Interest Rate Swap Quotes)** 來建構（Bootstrap）殖利率曲線。對於 CMS (Constant Maturity Swap) 產品而言，Swap Curve 是最核心的基礎。

```csharp
    HTS = dataReading.forwarding;
```

*   **財務工程視角：** 這裡將建構好的曲線賦值給變數 `HTS`。在量化程式庫的慣例中，`HTS` 通常代表 **Handle to Term Structure**（期限結構的指標或句柄）。而 `forwarding` 則明確指出這條曲線將被用作**遠期利率預測曲線 (Forwarding Curve)**。在區間計息 (Range Accrual) 商品中，模型需要計算每一天 (或每個觀察日) 的遠期指標利率（例如未來的 CMS 利率）是否落在指定的上下限區間內，這條 `forwarding` 曲線就是用來推導這些預期遠期利率的基礎。

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

---

## 三、 Libor Market Model (LMM) 建構與校準

```csharp
IborIndex Index = new USDLibor3M(HTS);
LiborForwardModelProcess process = new LiborForwardModelProcess(size, Index);
Handle<YieldTermStructure> termStructure = Index.forwardingTermStructure();
```

*   **財務工程視角：** 此段確立了模型的核心引擎為 **Libor Market Model (LMM)**。系統以 3 個月期的美元 Libor (`USDLibor3M`) 作為基礎指標利率，並將第一階段建構的期限結構 `HTS` 綁定進去。`LiborForwardModelProcess` 負責定義整個遠期利率的隨機過程狀態空間。

```csharp
LmVolatilityModel volaModel = new LmExtLinearExponentialVolModel(
    process.fixingTimes(), 0.5, 0.6, 0.1, 0.1);
LmCorrelationModel corrModel = new LmLinearExponentialCorrelationModel(size, 0.5, 0.8);
LiborForwardModel model = new LiborForwardModel(process, volaModel, corrModel);
```

*   **財務工程視角：** 這是 LMM 參數化的核心。
    *   `LmExtLinearExponentialVolModel`：採用了學理上著名的 **ABCD 波動度參數化模型** $\sigma(t) = [a + b(T-t)]e^{-c(T-t)} + d$。這能有效捕捉市場常見的波動度結構。
    *   `LmLinearExponentialCorrelationModel`：定義了遠期利率間的相關性結構，形式為 $\rho_{i,j} = e^{-\beta|T_i - T_j|}$，確保相近天期的利率連動性較高。

```csharp
LevenbergMarquardt om = new LevenbergMarquardt(1e-6, 1e-6, 1e-6);
model.calibrate(calibrationHelper, om, new EndCriteria(200, 100, 1e-6, 1e-6, 1e-6));
```

*   **財務工程視角：** 系統將挑選出的 Swaption 放入 `calibrationHelper` 中，並呼叫 `LevenbergMarquardt` 優化演算法進行全局校準，確保模型生成的價格能盡可能貼近市場真實報價。

---

## 四、 共變異數與漂移項推導 (Covariance & Drift Calculation)

```csharp
cov = Algorithm.ABCDVolCov(a, b, c, d, effStartTime, effStopTime, rateTimes[i], rateTimes[j]);
cov = adjK[i] * adjK[j] * cov;
GCOV[i, j] += cov * GCORR[i, j];
```

*   **財務工程視角：** 在進行蒙地卡羅模擬的每個時間步階，系統需計算利率間的共變異數。這裡實作了 LMM 模型中兩個遠期利率的瞬時共變異：$\text{Cov}(F_i, F_j) = \sigma_i \sigma_j \rho_{i,j}$。`ABCDVolCov` 解析了波動度的積分，而乘上 `GCORR` 則完成了共變異數矩陣 `GCOV` 的建構。

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

```csharp
double NPV = 0;
for (int i = 0; i < MSteps; i++)
{
    NPV += CF[i] * VDisc[i];
}
LNPV.Add(NPV);
```

*   **財務工程視角：** 最後，將所有未來的現金流乘上對應的折現因子 `VDisc`，加總得到單一路徑的淨現值 (NPV)，並將其存入列表 `LNPV` 以供最終蒙地卡羅平均。

---

## 八、 IMV 驗證重點提醒 (供後續查核)

1.  **測度轉換 (Measure Consistency)：** 程式採用 Spot Measure 演化，需確認最後的 `VDisc` 折現因子是否正確採用了具備路徑相依性 (Path-dependent) 的 Numéraire 進行折現，以免產生定價偏誤。
2.  **單一曲線 vs. 雙曲線架構 (Curve Framework)：** 系統是否具備 OIS Discounting 的雙曲線定價能力？驗證時需釐清預測曲線與折現曲線的分工。
3.  **Callable 機制缺失：** 目前所有的程式碼皆為處理「底層資產」的定價。必須有針對發行人提前解約權 (Bermudan Option) 的決策邏輯（如 Longstaff-Schwartz 演算法）。建議要求提供 LSMC 相關的程式碼片段進行下一步驗證。