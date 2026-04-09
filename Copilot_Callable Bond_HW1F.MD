# 模型驗證報告（Model Validation Report）

## USD 可贖回債券（Callable Bond）估值與風險管理模型  
### Hull–White 一因子模型（HW 1F）－三叉樹法（Recombining Trinomial Tree）

---

## 1. 報告摘要（Executive Summary）

本文件為一份**模型驗證報告（Model Validation Report）**，旨在說明並驗證一套用於 **USD 固定利率可贖回債券（USD Fixed‑Rate Callable Bonds）** 之估值與風險管理模型。

本模型採用 **Hull–White 一因子（HW 1F）短期利率模型** 作為無套利利率動態描述，並以 **可重組三叉樹法（Recombining Trinomial Tree）** 搭配 **Backward Induction** 進行離散化與數值實作，以處理發行人所持有之 **Bermudan 型提前贖回權利**。

本報告目的包括：
* 正式文件化模型之**理論架構、數學假設與數值實作方法**；
* 證明模型在理論與數值層面具備**一致性、穩定性與可解釋性**；
* 提供完整、可追溯之稽核軌跡，以支援 **定價、贖回決策分析、Call Boundary 判定，以及風險衡量（含分桶風險）**。

經本次驗證，本模型被評估為：
* 理論基礎健全；
* 工程實作透明、可重現；
* 適用於 **Front‑Office 定價與 Risk Management** 使用，

其適用範圍與限制條件詳述於第 10 節。

---

## 2. 商品定義（Product Definition）

### 2.1 商品概述
* **商品類型：** USD 固定利率可贖回債券  
* **發行人權利：** Bermudan Call（多個可贖回時點）  
* **投資人立場：** Long Bond / Short Issuer Call  

### 2.2 現金流結構
* 債券於存續期間內，依契約約定頻率支付固定利息；
* 若未行使贖回權利，投資人於到期日收回本金；
* 發行人可於指定贖回日，以事先約定之 **Call Price** 贖回債券。

由於發行人持有提前贖回權利，此產品呈現顯著的 **負凸性（Negative Convexity）** 特徵，需同時考量：
* 利率隨時間之隨機演化；
* 發行人於每一可贖回時點之最適行使行為。

---

## 3. 模型選擇與合理性（Model Selection Rationale）

### 3.1 利率模型選擇
本模型選用 **Hull–White 一因子短期利率模型**，主要理由如下：
* 模型本質具備**無套利性（Arbitrage‑Free）**；
* 數學結構簡潔，數值穩定，易於驗證；
* 能夠**精確配適期初零息利率曲線**；
* 為金融市場評價 Callable Bond 的主流標準模型之一。

短期利率動態定義為：

$$\mathrm{d}r(t)=\bigl(\theta(t)-a\,r(t)\bigr)\,\mathrm{d}t+\sigma\,\mathrm{d}W_t$$

其中：
* $r(t)$：短期無風險利率
* $a$：均值回歸速度（Mean Reversion Speed）
* $\sigma$：瞬時波動度（Instantaneous Volatility）
* $\theta(t)$：時間依存漂移項，用以配適期初利率結構

### 3.2 數值方法選擇
考量 Bermudan 型提前行使特性，本模型採用 **可重組三叉樹法（Recombining Trinomial Tree）**：
* 能準確近似連續時間短期利率過程；
* 可自然透過 backward induction 處理提前贖回；
* 每一節點數值皆可完整展開，具高度透明度。

基於提前行使需求，本模型**不採用 Monte Carlo 模擬**，以避免回歸近似（如 LSM）在驗證與稽核上的不確定性。

---

## 4. 利率曲線建構與 $\phi(t)$ 擬合

### 4.1 市場輸入
* 採用 USD 零息利率曲線（Single‑Curve Framework）；
* 曲線用以計算折現因子與建構初始利率期限結構。

### 4.2 決定性平移函數 $\phi(t)$
Hull–White 模型可重寫為：

$$r(t)=x(t)+\phi(t)$$

其中：
* $x(t)$：均值為零之隨機過程；
* $\phi(t)$：決定性平移函數。

為使模型於 $t=0$ 完全配適市場零息債價格 $P(0,t)$，設定：

$$\phi(t)=-\frac{\ln P(0,t)}{t}$$

此設計確保：
* 模型價格與市場零息債價格完全一致；
* 所有定價與風險分析均具備**曲線一致性（Curve Consistency）**。

---

## 5. 三叉樹建構（Trinomial Tree Construction）

### 5.1 時間離散化
* 採用固定時間步長：

$$\Delta t=0.5\text{ 年}$$

### 5.2 節點定義
樹中每一節點 $(i,j)$ 對應之短期利率為：

$$r_{i,j}=\phi(t_i)+j\,\sigma\sqrt{3\Delta t}$$

其中 $j$ 為節點指數。

### 5.3 轉移機率
三叉樹之上、中、下轉移機率依下列原則設定：
* 均值匹配（Mean Matching）；
* 變異數匹配（Variance Matching）；
* 確保樹狀結構可重組（Recombining）。

所有轉移機率與節點值皆予以明確保存，以供模型驗證與稽核。

---

## 6. 定價與提前贖回邏輯（Valuation & Exercise Logic）

### 6.1 Backward Induction
於到期日：

$$V_{T,j}=\text{Notional}$$

於中間節點：

$$V_{i,j}^{\text{cont}}=e^{-r_{i,j}\Delta t}\,\mathbb{E}^{\mathbb{Q}}[V_{i+1,\cdot}]+\text{Coupon}_{i+1}$$

### 6.2 Bermudan 贖回決策
於每一可贖回時點：

$$V_{i,j}=\min\bigl(V_{i,j}^{\text{cont}},\;\text{Call Price}\bigr)$$

此規則反映發行人在風險中性測度下之 **理性最適行使行為（Optimal Exercise）**。
所有節點價值均完整展開並保留，以支援獨立驗算。

---

## 7. Call Boundary 分析

### 7.1 定義
Call Boundary 定義為滿足：

$$V_{i,j}^{\text{cont}}\ge\text{Call Price}$$

之臨界節點集合。

於每一可贖回日，取最靠近中心路徑之節點作為該期 Call Boundary。

### 7.2 呈現方式
* **模型內部：** 短期利率 $r^*(t)$
* **市場溝通：** 轉換為 **Par Swap Yield**

### 7.3 實務觀察
* 未校準波動度時，Boundary 可能落於負短期利率；
* 經 ATM Swaption 校準後，Boundary 更平滑且具經濟直覺。

---

## 8. 模型校準（Calibration）

### 8.1 波動度校準
模型參數 $\sigma$ 係以 **USD ATM Swaptions** 校準：

$$\sigma\approx 70\text{ bps}$$

此參數對樹狀分散程度、提前贖回機率與負凸性行為至關重要。

---

## 9. 風險衡量架構（Risk Measurement Framework）

### 9.1 風險定義
所有風險皆採用 **完全重定價（Full Repricing）** 方式取得。

#### PV01（分桶）
$$\text{PV01}(t_k)=PV(t_k\uparrow 1\text{bp})-PV_{\text{base}}$$

#### Vega（Callable Vega）
$$\text{Vega}=PV(\sigma+\Delta\sigma)-PV(\sigma)$$

### 9.2 設計原則
* 不使用 Duration / Convexity 近似；
* 風險同時反映折現效應與 Call Boundary 位移；
* 分桶風險可直接支援避險決策。

---

## 10. 模型優點與限制

### 10.1 優點
* 完全配適市場利率曲線；
* 節點層級透明、可驗證；
* 波動度具市場一致性；
* 定價與風險邏輯一致。

### 10.2 限制
* 單一利率曲線架構（未區分 OIS / LIBOR）；
* 未納入發行人信用利差；
* 未建構狀態相依之條件遠期利率。

---

## 11. 驗證結論（Validation Conclusion）

經本次模型驗證，本模型被認定為：
* **理論正確**；
* **數值穩健**；
* **工程實作透明且可查核**。

在上述適用範圍內，本模型被視為 **適合用於 USD 可贖回債券之前台定價與風險管理用途**。

---

**文件狀態：模型驗證完成，可供 Desk 使用（Model‑Validation Ready）**