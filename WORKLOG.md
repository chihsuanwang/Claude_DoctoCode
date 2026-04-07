# CCDRA Pricing Project — 工作日誌

---

## 專案資訊

| 項目 | 內容 |
|------|------|
| 產品名稱 | 可贖回 CMS 連結每日區間計息債券 (CCDRA) |
| 評價方法 | LIBOR Market Model (LMM) + LSMC |
| 實作語言 | Python + QuantLib-Python |
| 技術文件 | `文件/技術文件.pdf` |
| 計畫文件 | `implementation_plan.md` |

---

## 進度總覽

| 階段 | 描述 | 狀態 | 完成日期 |
|------|------|------|---------|
| P0 | 讀取技術文件，梳理數學邏輯與定價流程 | ✅ 完成 | 2026-04-07 |
| P1 | 撰寫 `implementation_plan.md`（模組切分計畫）| ✅ 完成 | 2026-04-07 |
| P2 | 建立專案骨架（Class + Docstring，函數 pass）| ✅ 完成 | 2026-04-07 |
| P3 | 架構解耦審查，提出改善建議 | ✅ 完成 | 2026-04-07 |
| P4 | 重構骨架（依解耦建議調整）| ✅ 完成 | 2026-04-07 |
| P5 | Module 1 實作：市場資料與利率期間結構 | ✅ 完成 | 2026-04-07 |
| P6 | Module 2 實作：LMM 建構與市場校準 | ✅ 完成 | 2026-04-07 |
| P7 | Module 3 實作：MC 路徑生成與現金流 | ✅ 完成 | 2026-04-07 |
| P8 | Module 4 實作：LSMC 可贖回定價引擎 | ⬜ 待開始 | — |
| P9 | 整合測試：對齊文件範例（NPV ≈ 82.02）| ⬜ 待開始 | — |

---

## 詳細工作記錄

---

### 2026-04-07 — P0：技術文件分析

**完成內容：**
- 閱讀「技術文件.pdf」（可贖回 CMS 連結每日區間計息債券）
- 梳理核心數學邏輯：
  - LMM 動態方程（BGM 框架，Spot LIBOR Martingale 測度）
  - CMS 利率計算（Schlogl 內插法，Joshi 改良版）
  - 區間計息內插法（Ratio 公式）
  - LSMC 逆向歸納演算法
- 確認定價流程共 7 步驟

**關鍵參數（文件範例）：**
- 評價日：2022/3/31，到期日：2027/3/31
- CMS 參考：2Y CMS（USSW2），Libor 基礎：3M
- Floor = 0%，Ceiling = 4.25%，Coupon = 1.65%
- 預期輸出：CCDRA NPV ≈ 82.02，Call Value ≈ -7.81

---

### 2026-04-07 — P1：撰寫實作計畫

**完成內容：**
- 建立 `implementation_plan.md`
- 依 QuantLib 解耦哲學切分為 4 個模組
- 列出各模組所需 QuantLib 核心物件
- 繪製模組依賴關係圖

**設計決策：**
- 採用 MarketData → Model → Instrument/Path → Engine 四層結構
- 模組間透過 `RelinkableHandle` 單向依賴，不直接持有物件

---

### 2026-04-07 — P2：建立專案骨架

**建立的檔案：**

| 檔案 | Class 數 | Function 數 | 說明 |
|------|---------|------------|------|
| `src/instrument.py` | 1 (`CCDRASpec`) | 4 | 契約規格資料容器 |
| `src/market_data.py` | 3 | 11 | 市場資料 + 利率期間結構 |
| `src/lmm_model.py` | 4 | 9 | LMM 建構 + 校準 |
| `src/mc_engine.py` | 4 | 12 | MC 路徑生成 + 現金流 |
| `src/pricing_engine.py` | 4 | 9 | LSMC 定價引擎 |
| `src/__init__.py` | — | — | 套件宣告 |
| `main.py` | — | 2 | 主程式入口 |

**總計：** 16 個 class，47 個 function（含 docstring，函數內容為 pass）

---

### 2026-04-07 — P3：架構解耦審查

**發現的耦合問題（共 5 類）：**

詳見下方「架構問題清單」。

**決議：重構骨架（P4），調整為 9 個檔案結構。**

---

## 架構問題清單（P3 審查結果）

### 問題 1：`instrument.py` 混合資料與行為

**現象：** `CCDRASpec` 是 dataclass，但含有 `payment_dates()`、`call_dates()`、`freeze_end_date()` 等方法，這些方法內部需要 QL Calendar / Schedule 物件。

**問題：** 純資料容器（Data Object）不應依賴 QL，否則：
- 單元測試必須有 QL 環境
- 無法跨系統序列化（JSON/DB）

**建議：** 將排程邏輯抽到獨立的 `schedule.py`（`CCDRAScheduleBuilder`）。

---

### 問題 2：`mc_engine.py` 職責過多（3 合 1）

**現象：** 同一個檔案同時包含：
- `CMSRateCalculator`（純數學，只需 numpy）
- `RangeAccrualInterpolator`（純數學，只需 numpy）
- `MonteCarloEngine`（QL-heavy，需要 LiborForwardModel）

**問題：**
- 純數學模組不應與 QL 重度依賴放在一起
- 無法對 CMS 計算與 Ratio 計算進行獨立單元測試
- 違反 Single Responsibility Principle

**建議：** 拆成獨立的 `rate_calculator.py`（pure numpy）。

---

### 問題 3：`SimulationResult` 放在 `mc_engine.py` 造成跨模組耦合

**現象：** `pricing_engine.py` 必須 `from .mc_engine import SimulationResult`，造成 Module 4 直接依賴 Module 3。

**問題：** 若 Module 3 內部重組，Module 4 的 import 也必須跟著修改。

**建議：** 所有跨模組共用的資料容器移至中立的 `types.py`。

---

### 問題 4：`MonteCarloEngine` 建構子注入 6 個具體物件

**現象：**
```python
MonteCarloEngine(model, discount_handle, spec,
                 cms_calculator, range_interpolator, sim_config)
```

**問題：** 直接依賴具體型別（`ql.LiborForwardModel`、`CMSRateCalculator`）。若要替換模型或計算方式，必須修改 `MonteCarloEngine` 本身。

**建議：** 在 `protocols.py` 定義 Protocol 介面（`RateModelProtocol`、`CMSCalculatorProtocol`），`MonteCarloEngine` 依賴介面而非具體類別。

---

### 問題 5：缺乏統一的抽象介面層

**現象：** 各模組直接依賴對方的具體 class，沒有 Protocol / ABC 層。

**問題：** 未來若要替換 LMM 為 HJM 或 Sabr 模型，需要修改多個下游檔案。

**建議：** 新增 `protocols.py` 定義所有模組間的介面契約。

---

## 建議的重構後檔案結構（P4 目標）

```
src/
├── types.py            ← 所有跨模組共用資料容器（無 QL import）
├── protocols.py        ← 抽象介面定義（Protocol / ABC）
├── instrument.py       ← 純資料 dataclass（移除所有方法）
├── schedule.py         ← 日期排程計算（CCDRAScheduleBuilder）
├── market_data.py      ← Module 1：市場資料與利率期間結構
├── lmm_model.py        ← Module 2：LMM 建構與市場校準
├── rate_calculator.py  ← CMS 計算 + 區間計息（pure numpy，可獨立測試）
├── mc_engine.py        ← Module 3：MC 路徑演化（QL-heavy）
└── pricing_engine.py   ← Module 4：LSMC 定價引擎（pure numpy）
```

### 調整後的依賴關係

```
types.py, protocols.py
    ↓（被所有模組 import）
instrument.py  →  schedule.py
    ↓
market_data.py
    ↓ RelinkableHandle
lmm_model.py
    ↓ RateModelProtocol（介面）
rate_calculator.py  ←──────┐
    ↓ CMSCalculatorProtocol │
mc_engine.py ───────────────┘
    ↓ SimulationResult（types.py）
pricing_engine.py
```

---

## 待決議事項

| 編號 | 問題 | 狀態 |
|------|------|------|
| Q1 | `schedule.py` 是否需要支援 Modified Following 日期調整？ | ⬜ 待確認 |
| Q2 | 市場資料 CSV 格式是否固定為 Bloomberg 輸出？或需支援手動輸入？ | ⬜ 待確認 |
| Q3 | Monte Carlo 路徑數量：開發期用 1,000，生產用 10,000？ | ⬜ 待確認 |
| Q4 | 是否需要輸出希臘字母（DV01、Duration）？ | ⬜ 待確認 |
| Q5 | 折現曲線：使用單一 Swap 曲線？還是 OIS 折現 + Libor 遠期分離？| ⬜ 待確認 |

---

## 重要參考數值（驗證基準）

| 參數 | 數值 | 來源 |
|------|------|------|
| LMM Calibration a | 0.133 | 技術文件 p.18 |
| LMM Calibration b | 0.535 | 技術文件 p.18 |
| LMM Calibration c | 0.567 | 技術文件 p.18 |
| LMM Calibration d | 0.120 | 技術文件 p.18 |
| rho | 0.611 | 技術文件 p.18 |
| beta | 0.599 | 技術文件 p.18 |
| CCDRA NPV | 82.0232 | 技術文件 p.20 |
| Call Value | -7.8104 | 技術文件 p.20 |
