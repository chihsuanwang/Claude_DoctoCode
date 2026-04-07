# 技術文件：Libor Market Model (LMM) 之模型設定與市場校準框架

**文件版次：** 1.0
**主題：** 基於 QuantLib 框架之 LMM/BGM 模型參數校準 (Calibration) 實作解析
**撰寫人：** 財務工程部 (Financial Engineering)

---

## 1. 引言 (Introduction)
本文件旨在說明 Libor Market Model (LMM)，亦稱為 Brace-Gatarek-Musiela (BGM) 模型，於定價系統中之底層建構邏輯與校準程序。該模型透過對離散期限結構（Discrete Term Structure）中一系列遠期利率的聯合動態過程進行建模，為複雜利率衍生性商品（如 Callable IRS、Bermudan Swaptions）提供無套利（Arbitrage-Free）的定價基礎。

本實作基於 QuantLib (C# wrapper) 進行開發，主要涵蓋模型參數化設定、市場基準工具（Benchmark Instruments）綁定，以及非線性最佳化求解器之應用。

## 2. 模型動態與參數化設定 (Model Specification)

在 LMM 框架下，遠期利率 $L_i(t)$ 之隨機微分方程 (SDE) 在遠期測度 (Forward Measure) 下可表述為：
$$dL_i(t) = \dots dt + \sigma_i(t) L_i(t) dW_i(t)$$

系統實作中，共變異數結構（Covariance Structure）被解耦為「波動率模型」與「相關係數模型」兩獨立元件，以確保參數估計的穩定性。

### 2.1 波動率模型 (Volatility Structure)
程式碼中採用了擴展型線性指數波動率模型（`LmExtLinearExponentialVolModel`），其核心基於 Rebonato 提出的參數型波動率函數：
$$\sigma(t, T_i) = [a + b(T_i - t)] e^{-c(T_i - t)} + d$$
此四參數模型具備精確捕捉市場隱含波動率曲面（Volatility Surface）特徵之能力，特別是利率市場中常見的「波動率駝峰（Volatility Hump）」現象。在實作中，初始猜測值被設定為 $(a, b, c, d) = (0.5, 0.6, 0.1, 0.1)$。

### 2.2 相關係數模型 (Correlation Structure)
為確保遠期利率間之相關性矩陣（Correlation Matrix）具備正定性（Positive Definiteness）且符合距離遞減之市場實務，系統採用了線性指數相關係數模型（`LmLinearExponentialCorrelationModel`）：
$$\rho_{i,j} = \rho_{\infty} + (1 - \rho_{\infty}) e^{-\beta |T_i - T_j|}$$
該函數確保相鄰到期日之利率具備高度相關性，且隨時間間距增加而呈現指數型衰減。程式中設定之初始參數為 $(\rho_{\infty}, \beta) = (0.5, 0.8)$。

## 3. 市場工具校準 (Market Calibration)

模型校準之核心目標，係尋找一組模型參數 $\Theta^*$，使模型所計算出之衍生性商品價格 $V^{model}(\Theta)$ 與市場真實報價 $V^{market}$ 間之誤差最小化。

### 3.1 輔助校準工具 (Calibration Helpers)
為進行校準，系統自市場數據中提取一系列標準交換息選擇權（European Swaptions），並將其轉換為 `SwaptionHelper` 物件。
* 系統遍歷 `listSwaption`，動態生成包含選擇權到期日 (`matlen`)、交換約期 (`swaplen`) 以及市場隱含波動率 (`swaptionVol`) 的標靶工具。
* 每一個 Helper 皆被指派獨立之定價引擎 `LfmSwaptionEngine`，該引擎透過 Black 漸近近似法（Black's Approximation）快速計算 LMM 框架下的 Swaption 理論價格，從而大幅提升校準效率。

## 4. 最佳化演算法 (Optimization Algorithm)

校準過程本質上為一多變數非線性最小平方法（Non-linear Least Squares）問題。目標函數定義為：
$$\min_{\Theta} \sum_{k=1}^{N} w_k \left( V_k^{model}(\Theta) - V_k^{market} \right)^2$$

### 4.1 Levenberg-Marquardt 求解器
為確保收斂之穩定性與速度，實作中呼叫了 `LevenbergMarquardt` 演算法。該演算法結合了梯度下降法（Gradient Descent）與高斯-牛頓法（Gauss-Newton Method）的優勢，特別適合處理 LMM 參數間高度非線性及可能存在局部極小值（Local Minima）的情況。
* 演算法之精度參數均設定為 `1e-6`。

### 4.2 收斂條件 (End Criteria)
模型執行 `.calibrate()` 時，傳入了 `EndCriteria` 以規範迭代之上限與停止閥值：
* 最大迭代次數（Max Iterations）：200 次
* 穩態迭代次數（Stationary Iterations）：100 次
* 根誤差閥值（Root Epsilon）：`1e-6`
* 函數值閥值（Function Epsilon）：`1e-6`
* 梯度閥值（Gradient Norm Epsilon）：`1e-6`

## 5. 結論與風控考量 (Conclusion & Risk Management considerations)
本模組完成校準後，所生成之 `LiborForwardModel` 物件將完整內含擬合當下市場狀態之波動率與相關性參數。此實體後續可無縫接軌至蒙地卡羅模擬引擎（Monte Carlo Engine），為具有路徑相依（Path-dependent）或多重執行權之衍生性金融商品提供穩健且符合無套利假設之公允價值（Fair Value）與風險敏感度（Greeks）分析。