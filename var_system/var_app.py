"""
var_app.py — 市場風險值（VaR）計算系統 Streamlit 介面
=====================================================
啟動方式：
    streamlit run var_app.py

功能：
  1. 匯入歷史價格（Excel / CSV，本地幣別）
  2. 設定多市場部位（台股 / 美股 / 韓股，自動識別幣別）
  3. 設定即期匯率 & 選擇上傳 FX 歷史（USD/TWD、KRW/TWD...）
  4. 選擇計算方法、信賴水準、持有天數
  5. 顯示 VaR / CVaR / Component VaR（統一以 TWD 計）
  6. P&L 分布圖、相關性熱圖、回測圖
  7. 匯出報告
"""

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import io
import warnings

from var_engine import (
    VaRCalculator, Position, VaRResult,
    detect_asset_currency, get_market_label, DEFAULT_SPOT_FX,
)

warnings.filterwarnings("ignore")

# ─────────────────────────────────────────────────────────────
# 頁面設定
# ─────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="市場風險值計算系統（VaR）",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────────────────────
# 自訂 CSS
# ─────────────────────────────────────────────────────────────

st.markdown("""
<style>
    .main-title {
        font-size: 2rem; font-weight: 800;
        color: #1F497D; margin-bottom: 0.2rem;
    }
    .sub-title {
        font-size: 0.95rem; color: #666; margin-bottom: 1.5rem;
    }
    .metric-box {
        background: transparent; border-radius: 8px;
        border: 1px solid #1F497D; border-left: 4px solid #1F497D;
        padding: 1rem 1.2rem;
    }
    .metric-label { font-size: 0.8rem; color: #555; font-weight: 600; }
    .metric-value { font-size: 1.6rem; font-weight: 800; color: #C00000; }
    .metric-value-blue { font-size: 1.6rem; font-weight: 800; color: #1F497D; }
    .section-header {
        font-size: 1.1rem; font-weight: 700; color: #1F497D;
        border-bottom: 2px solid #1F497D; padding-bottom: 4px;
        margin-top: 1.5rem; margin-bottom: 0.8rem;
    }
    .warning-box {
        background: transparent; border: 1px solid #FF9800;
        border-left: 4px solid #FF9800;
        padding: 0.7rem 1rem; border-radius: 4px;
        font-size: 0.85rem; color: inherit;
    }
    .success-box {
        background: transparent; border: 1px solid #4CAF50;
        border-left: 4px solid #4CAF50;
        padding: 0.7rem 1rem; border-radius: 4px;
        font-size: 0.85rem; color: inherit;
    }
    .info-box {
        background: transparent; border: 1px solid #2196F3;
        border-left: 4px solid #2196F3;
        padding: 0.7rem 1rem; border-radius: 4px;
        font-size: 0.85rem; color: inherit;
    }
    div[data-testid="stDataFrame"] { border-radius: 8px; }
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────
# 標題
# ─────────────────────────────────────────────────────────────

st.markdown('<div class="main-title">📊 市場風險值計算系統</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="sub-title">Value at Risk (VaR) | Historical Simulation · Parametric · Monte Carlo'
    ' ｜ 支援台股 🇹🇼 · 美股 🇺🇸 · 韓股 🇰🇷 多市場</div>',
    unsafe_allow_html=True,
)

# ─────────────────────────────────────────────────────────────
# 側邊欄：參數設定
# ─────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("### ⚙️ 計算參數設定")
    st.divider()

    method = st.selectbox(
        "計算方法",
        options=["Historical Simulation", "Parametric (Delta-Normal)", "Monte Carlo"],
        help=(
            "**Historical Simulation**：使用實際歷史報酬，不假設分布形態\n\n"
            "**Parametric**：假設常態分布，計算速度快，適合流動性佳的資產\n\n"
            "**Monte Carlo**：模擬大量路徑，最靈活，適合非線性部位"
        )
    )

    confidence = st.selectbox(
        "信賴水準",
        options=[0.95, 0.99, 0.999],
        index=1,
        format_func=lambda x: f"{x*100:.1f}%",
        help="Basel III 市場風險最低資本要求採用 99%"
    )

    horizon = st.selectbox(
        "持有天數（Horizon）",
        options=[1, 5, 10, 21, 60],
        index=0,
        format_func=lambda x: (
            f"{x} 天（{'1 日' if x==1 else '1 週' if x==5 else '2 週' if x==10 else '1 月' if x==21 else '3 月'}）"
        ),
        help="Basel III 要求計算 10 日 VaR"
    )

    lookback = st.slider(
        "歷史回溯天數",
        min_value=60, max_value=1000, value=252, step=20,
        help="Basel III 要求至少 250 個交易日（≈1年）"
    )

    st.divider()
    st.markdown("### 🔧 進階設定")

    use_ewma = st.checkbox("使用 EWMA 共變異數", value=False,
                            help="RiskMetrics λ=0.94，對近期資料給予較高權重")
    if use_ewma:
        ewma_lam = st.slider("EWMA λ", 0.85, 0.99, 0.94, 0.01)
    else:
        ewma_lam = 0.94

    if method == "Monte Carlo":
        n_sims = st.select_slider(
            "模擬路徑數",
            options=[1000, 5000, 10000, 50000],
            value=10000,
            help="路徑越多越精確，但計算時間越長"
        )
    else:
        n_sims = 10000

    return_type = st.radio(
        "報酬率計算方式",
        options=["log", "pct"],
        format_func=lambda x: "對數報酬 (log return)" if x == "log" else "簡單報酬 (% change)",
        horizontal=True,
    )

    st.divider()
    st.markdown("#### 📋 操作說明")
    st.markdown("""
1. **上傳歷史價格** 或使用範例資料
2. **設定匯率**（有外幣部位時）
3. **輸入部位**（標的 + 數量）
4. 設定計算參數
5. 點擊「**🚀 執行 VaR 計算**」
    """)

# ─────────────────────────────────────────────────────────────
# Tab 設定
# ─────────────────────────────────────────────────────────────

tab1, tab2, tab3, tab4 = st.tabs([
    "📥 資料匯入 & 部位設定",
    "📈 VaR 計算結果",
    "📉 風險分析圖表",
    "🔍 回測 & 壓力測試"
])

# ─────────────────────────────────────────────────────────────
# Tab 1：資料匯入
# ─────────────────────────────────────────────────────────────

with tab1:
    col_left, col_right = st.columns([1, 1], gap="large")

    with col_left:
        st.markdown('<div class="section-header">📂 歷史價格資料匯入</div>', unsafe_allow_html=True)

        data_source = st.radio(
            "資料來源",
            options=["上傳檔案（Excel / CSV）", "使用內建多市場範例資料"],
            horizontal=True
        )

        prices_df = None

        if data_source == "上傳檔案（Excel / CSV）":
            st.markdown("""
<div class="warning-box">
📌 <b>檔案格式要求：</b><br>
• 第一欄為日期（Date），格式 YYYY-MM-DD<br>
• 其餘各欄為標的收盤價（<b>本地幣別</b>），欄名即為 Yahoo Finance ticker<br>
• 台股：2330.TW ｜ 美股：AAPL ｜ 韓股：005930.KS<br>
• 支援 .xlsx / .xls / .csv
</div>
""", unsafe_allow_html=True)
            st.markdown("")

            uploaded_file = st.file_uploader(
                "拖曳或點擊上傳價格檔案",
                type=["xlsx", "xls", "csv"],
                label_visibility="collapsed"
            )

            if uploaded_file:
                try:
                    if uploaded_file.name.endswith(".csv"):
                        df = pd.read_csv(uploaded_file, index_col=0, parse_dates=True)
                    else:
                        df = pd.read_excel(uploaded_file, index_col=0, parse_dates=True)

                    df = df.sort_index().dropna(how="all")
                    df = df.apply(pd.to_numeric, errors="coerce")
                    prices_df = df

                    st.markdown(f"""
<div class="success-box">
✅ 成功載入：<b>{len(df)} 個交易日 × {len(df.columns)} 個標的</b><br>
期間：{df.index[0].strftime('%Y-%m-%d')} ～ {df.index[-1].strftime('%Y-%m-%d')}
</div>
""", unsafe_allow_html=True)
                    st.dataframe(df.tail(5).style.format("{:.2f}"), use_container_width=True)

                except Exception as e:
                    st.error(f"❌ 檔案載入失敗：{e}")

        else:
            # ── 內建多市場範例資料 ──
            np.random.seed(42)
            n_days = 504
            dates = pd.bdate_range(end="2024-12-31", periods=n_days)

            # 台股（TWD）
            tw_assets  = {"台積電(2330.TW)": 600.0,  "聯發科(2454.TW)": 900.0,  "鴻海(2317.TW)": 110.0}
            tw_mu      = [0.0008,  0.0010,  0.0005]
            tw_sigma   = [0.020,   0.025,   0.018]

            # 美股（USD，約 32.5 TWD/USD）
            us_assets  = {"AAPL": 185.0,  "MSFT": 420.0}
            us_mu      = [0.0007,  0.0008]
            us_sigma   = [0.018,   0.016]

            # 韓股（KRW，約 0.0235 TWD/KRW）
            kr_assets  = {"Samsung(005930.KS)": 70000.0, "SKHynix(000660.KS)": 180000.0}
            kr_mu      = [0.0006,  0.0009]
            kr_sigma   = [0.022,   0.028]

            # 相關矩陣（7×7：3 TW + 2 US + 2 KR）
            # 同市場高相關，跨市場中等相關
            corr_base = np.array([
                # TW1  TW2  TW3  US1  US2  KR1  KR2
                [1.00, 0.55, 0.40, 0.30, 0.28, 0.20, 0.18],  # TW1
                [0.55, 1.00, 0.35, 0.25, 0.22, 0.18, 0.15],  # TW2
                [0.40, 0.35, 1.00, 0.20, 0.18, 0.15, 0.12],  # TW3
                [0.30, 0.25, 0.20, 1.00, 0.65, 0.25, 0.22],  # US1
                [0.28, 0.22, 0.18, 0.65, 1.00, 0.22, 0.20],  # US2
                [0.20, 0.18, 0.15, 0.25, 0.22, 1.00, 0.60],  # KR1
                [0.18, 0.15, 0.12, 0.22, 0.20, 0.60, 1.00],  # KR2
            ])

            all_mu    = tw_mu    + us_mu    + kr_mu
            all_sigma = tw_sigma + us_sigma + kr_sigma
            cov_mat   = np.diag(all_sigma) @ corr_base @ np.diag(all_sigma)
            L_chol    = np.linalg.cholesky(cov_mat)

            z_sim = np.random.standard_normal((len(all_mu), n_days))
            ret_sim = (L_chol @ z_sim).T + np.array(all_mu)

            all_s0 = (list(tw_assets.values()) + list(us_assets.values())
                      + list(kr_assets.values()))
            all_names = (list(tw_assets.keys()) + list(us_assets.keys())
                         + list(kr_assets.keys()))

            prices_local = {}
            for i, (name, s0) in enumerate(zip(all_names, all_s0)):
                prices_local[name] = s0 * np.exp(np.cumsum(ret_sim[:, i]))
            prices_df = pd.DataFrame(prices_local, index=dates)

            # 模擬 FX 歷史（USD/TWD、KRW/TWD）
            fx_mu_usd, fx_sig_usd = 0.0000, 0.003
            fx_mu_krw, fx_sig_krw = 0.0000, 0.004
            usd_twd_series = 32.5 * np.exp(np.cumsum(
                np.random.normal(fx_mu_usd, fx_sig_usd, n_days)))
            krw_twd_series = 0.0235 * np.exp(np.cumsum(
                np.random.normal(fx_mu_krw, fx_sig_krw, n_days)))
            fx_sample_df = pd.DataFrame(
                {"USD": usd_twd_series, "KRW": krw_twd_series},
                index=dates,
            )
            st.session_state["fx_prices_sample"] = fx_sample_df

            st.markdown(f"""
<div class="success-box">
✅ 內建多市場範例資料已載入：<b>{n_days} 個交易日 × {len(all_names)} 個標的</b><br>
台股 🇹🇼（TWD）: {', '.join(tw_assets.keys())}<br>
美股 🇺🇸（USD）: {', '.join(us_assets.keys())}<br>
韓股 🇰🇷（KRW）: {', '.join(kr_assets.keys())}<br>
<small>模擬資料，僅供示範，已同步生成 USD/TWD、KRW/TWD 匯率歷史</small>
</div>
""", unsafe_allow_html=True)

            st.dataframe(
                prices_df.tail(3).style.format("{:.2f}"),
                use_container_width=True,
            )

            # 下載範例 Excel
            buf = io.BytesIO()
            prices_df.reset_index().rename(columns={"index": "Date"}).to_excel(buf, index=False)
            st.download_button(
                "⬇️ 下載範例價格 Excel 格式",
                data=buf.getvalue(),
                file_name="sample_prices_multi_market.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

    # ── 部位輸入 ──
    with col_right:
        st.markdown('<div class="section-header">📋 部位設定（Position Entry）</div>',
                    unsafe_allow_html=True)

        if prices_df is not None:
            available = list(prices_df.columns)

            # 顯示各標的市場
            market_tags = " | ".join(
                f"{n}({get_market_label(n)})" for n in available
            )
            st.caption(f"已識別標的市場：{market_tags}")
            st.divider()

            st.markdown("**方式一：表格直接輸入**")

            default_rows = [
                {
                    "標的名稱": name,
                    "持有數量": 100,
                    "幣別": detect_asset_currency(name),
                }
                for name in available[:6]
            ]
            position_df = st.data_editor(
                pd.DataFrame(default_rows),
                num_rows="dynamic",
                column_config={
                    "標的名稱": st.column_config.SelectboxColumn(
                        "標的名稱",
                        options=available,
                        required=True,
                    ),
                    "持有數量": st.column_config.NumberColumn(
                        "持有數量",
                        help="正數=多頭，負數=空頭",
                        min_value=-1e9,
                        max_value=1e9,
                        format="%d",
                        required=True,
                    ),
                    "幣別": st.column_config.SelectboxColumn(
                        "幣別",
                        options=["TWD", "USD", "KRW", "HKD", "JPY", "CNY"],
                        help="系統依 ticker 自動識別，可手動覆蓋",
                        required=True,
                    ),
                },
                use_container_width=True,
                height=280,
            )

            st.markdown("**方式二：上傳部位 CSV**")
            st.caption("格式：標的名稱, 持有數量, 幣別")

            # 下載範例 CSV（含幣別欄位）
            _s_names = list(available[:min(4, len(available))])
            _sample_df = pd.DataFrame({
                "標的名稱": _s_names,
                "持有數量": [100, -50, 200, 300][:len(_s_names)],
                "幣別": [detect_asset_currency(n) for n in _s_names],
            })
            _csv_bytes = _sample_df.to_csv(index=False).encode("utf-8-sig")
            st.download_button(
                label="⬇️ 下載範例 CSV",
                data=_csv_bytes,
                file_name="sample_positions.csv",
                mime="text/csv",
            )

            pos_file = st.file_uploader("上傳部位檔（CSV）", type=["csv"],
                                         label_visibility="collapsed")
            if pos_file:
                pos_csv = pd.read_csv(pos_file)
                if {"標的名稱", "持有數量"}.issubset(pos_csv.columns):
                    if "幣別" not in pos_csv.columns:
                        pos_csv["幣別"] = pos_csv["標的名稱"].apply(detect_asset_currency)
                    position_df = pos_csv
                    st.success(f"✅ 已匯入 {len(pos_csv)} 筆部位")
                else:
                    st.error("CSV 需包含欄位：標的名稱, 持有數量")

            # ── 部位市值彙整（以基礎幣別 TWD 顯示）──
            st.divider()
            if not position_df.empty:
                st.markdown("**部位市值彙整（以 TWD 計）：**")
                latest = prices_df.iloc[-1]
                # 取得即期匯率（優先取 session state 中使用者設定的值）
                _spot = st.session_state.get("spot_fx_input", {
                    "USD": 32.5, "KRW": 0.0235, "HKD": 4.15, "JPY": 0.22, "TWD": 1.0
                })
                summary = []
                for _, row in position_df.iterrows():
                    name = row["標的名稱"]
                    qty  = float(row["持有數量"])
                    ccy  = str(row.get("幣別", detect_asset_currency(name)))
                    if name in latest.index and qty != 0:
                        local_price = latest[name]
                        fx_rate = _spot.get(ccy, 1.0)
                        local_mv = qty * local_price
                        twd_mv   = local_mv * fx_rate
                        summary.append({
                            "標的": name,
                            "市場": get_market_label(name),
                            "幣別": ccy,
                            f"本地價格({ccy})": f"{local_price:,.2f}",
                            "持有數量": f"{qty:+,.0f}",
                            f"本地市值({ccy})": f"{local_mv:+,.0f}",
                            "TWD 市值": f"{twd_mv:+,.0f}",
                            "方向": "多頭 📈" if qty > 0 else "空頭 📉",
                        })
                if summary:
                    st.dataframe(pd.DataFrame(summary), use_container_width=True)
                    total_twd = sum(
                        float(r["TWD 市值"].replace(",", "").replace("+", ""))
                        for r in summary
                    )
                    st.metric("組合淨市值（TWD）", f"{total_twd:+,.0f}")
        else:
            st.info("請先在左側選擇或上傳價格資料")

    # ── FX 匯率設定（跨欄，全寬）──
    st.divider()
    st.markdown('<div class="section-header">🌐 外幣匯率設定（多市場必填）</div>',
                unsafe_allow_html=True)

    with st.expander("展開設定匯率 / 上傳 FX 歷史", expanded=True):
        st.markdown("""
<div class="info-box">
台幣為基礎計算幣別（TWD）。持有美股或韓股時，請設定即期匯率，系統將自動換算市值與 VaR。
<br>若上傳 FX 歷史序列，系統會將匯率波動納入外幣資產的回報計算中（更精確）。
</div>
""", unsafe_allow_html=True)

        col_fx1, col_fx2, col_fx3, col_fx4 = st.columns(4)
        with col_fx1:
            usd_rate = st.number_input(
                "USD/TWD（1 美元 = ? 台幣）",
                value=32.5, min_value=1.0, max_value=100.0, step=0.1,
                format="%.2f",
            )
        with col_fx2:
            krw_rate = st.number_input(
                "KRW/TWD（1 韓元 = ? 台幣）",
                value=0.0235, min_value=0.001, max_value=1.0, step=0.0001,
                format="%.4f",
            )
        with col_fx3:
            hkd_rate = st.number_input(
                "HKD/TWD（1 港元 = ? 台幣）",
                value=4.15, min_value=1.0, max_value=20.0, step=0.01,
                format="%.2f",
            )
        with col_fx4:
            jpy_rate = st.number_input(
                "JPY/TWD（1 日元 = ? 台幣）",
                value=0.22, min_value=0.01, max_value=2.0, step=0.001,
                format="%.3f",
            )

        spot_fx_input = {
            "TWD": 1.0, "USD": usd_rate, "KRW": krw_rate,
            "HKD": hkd_rate, "JPY": jpy_rate,
        }
        st.session_state["spot_fx_input"] = spot_fx_input

        st.markdown("---")
        st.markdown("**FX 歷史序列上傳（選填，納入 FX 風險計算）**")
        st.caption(
            "格式：第一欄 Date（YYYY-MM-DD），其餘欄位為幣別代碼（USD / KRW / HKD / JPY），"
            "值為 1 外幣 = ? 台幣"
        )

        col_fxup1, col_fxup2 = st.columns([2, 1])
        with col_fxup1:
            fx_file = st.file_uploader(
                "上傳 FX 歷史 CSV / Excel",
                type=["csv", "xlsx"],
                label_visibility="collapsed",
                key="fx_upload",
            )
        with col_fxup2:
            # 下載 FX 範例
            if "fx_prices_sample" in st.session_state:
                _fx_sample = st.session_state["fx_prices_sample"]
                _fx_buf = _fx_sample.reset_index().rename(
                    columns={"index": "Date"}
                ).to_csv(index=False).encode("utf-8-sig")
                st.download_button(
                    "⬇️ 下載 FX 範例 CSV",
                    data=_fx_buf,
                    file_name="sample_fx_rates.csv",
                    mime="text/csv",
                )

        fx_prices_df = None
        if fx_file:
            try:
                if fx_file.name.endswith(".csv"):
                    fx_prices_df = pd.read_csv(fx_file, index_col=0, parse_dates=True)
                else:
                    fx_prices_df = pd.read_excel(fx_file, index_col=0, parse_dates=True)
                fx_prices_df = fx_prices_df.sort_index().dropna(how="all")
                st.success(f"✅ FX 歷史已載入：{len(fx_prices_df)} 筆，欄位：{list(fx_prices_df.columns)}")
            except Exception as e:
                st.error(f"FX 檔案載入失敗：{e}")
        elif "fx_prices_sample" in st.session_state and data_source == "使用內建多市場範例資料":
            fx_prices_df = st.session_state["fx_prices_sample"]
            st.caption("已自動使用內建範例的模擬 FX 歷史（USD/TWD、KRW/TWD）")

        st.session_state["fx_prices_df"] = fx_prices_df

    # 儲存到 session state
    st.session_state["prices_df"] = prices_df
    if prices_df is not None and "position_df" in dir() and not position_df.empty:
        st.session_state["position_df"] = position_df

    st.divider()
    run_btn = st.button("🚀 執行 VaR 計算", type="primary", use_container_width=True)

    if run_btn:
        if prices_df is not None and "position_df" in dir() and not position_df.empty:
            st.session_state["run_calc"] = True
            st.session_state["position_df"] = position_df
            st.session_state["calc_params"] = {
                "method":      method,
                "confidence":  confidence,
                "horizon":     horizon,
                "lookback":    lookback,
                "use_ewma":    use_ewma,
                "ewma_lam":    ewma_lam,
                "n_sims":      n_sims,
                "return_type": return_type,
                "spot_fx":     spot_fx_input,
            }
        else:
            st.error("⚠️ 請先載入價格資料並設定部位")

# ─────────────────────────────────────────────────────────────
# 計算執行
# ─────────────────────────────────────────────────────────────

result: VaRResult | None = None

if (st.session_state.get("run_calc")
        and "prices_df" in st.session_state
        and "position_df" in st.session_state):

    prices_df  = st.session_state["prices_df"]
    pos_df     = st.session_state["position_df"]
    params     = st.session_state["calc_params"]
    fx_prices  = st.session_state.get("fx_prices_df")
    spot_fx    = params.get("spot_fx", {})

    # 建立 Position 物件（帶幣別）
    positions = []
    for _, row in pos_df.iterrows():
        name = str(row["標的名稱"])
        qty  = float(row["持有數量"])
        ccy  = str(row.get("幣別", detect_asset_currency(name)))
        if name in prices_df.columns and qty != 0:
            positions.append(Position(name=name, quantity=qty, currency=ccy))

    if not positions:
        st.error("⚠️ 沒有有效的部位，請確認標的名稱與數量")
    else:
        try:
            with st.spinner("計算中..."):
                calc = VaRCalculator(
                    prices=prices_df,
                    positions=positions,
                    return_method=params["return_type"],
                    ewma=params["use_ewma"],
                    ewma_lambda=params["ewma_lam"],
                    base_currency="TWD",
                    fx_prices=fx_prices,
                    spot_fx=spot_fx,
                )

                if params["method"] == "Historical Simulation":
                    result = calc.historical(
                        confidence=params["confidence"],
                        horizon=params["horizon"],
                        lookback=params["lookback"],
                    )
                elif params["method"] == "Parametric (Delta-Normal)":
                    result = calc.parametric(
                        confidence=params["confidence"],
                        horizon=params["horizon"],
                        lookback=params["lookback"],
                    )
                else:
                    result = calc.monte_carlo(
                        confidence=params["confidence"],
                        horizon=params["horizon"],
                        n_sims=params["n_sims"],
                        lookback=params["lookback"],
                    )

                # FX 納入警告
                if fx_prices is None or (hasattr(fx_prices, "empty") and fx_prices.empty):
                    _foreign = [n for n, c in calc.asset_currencies.items() if c != "TWD"]
                    if _foreign:
                        st.warning(
                            f"⚠️ 外幣資產 {_foreign} 的 FX 匯率波動未納入回報計算（未上傳 FX 歷史）。"
                            "VaR 僅反映本地股價風險，會低估整體風險。"
                        )

                st.session_state["result"] = result
                st.session_state["calc_obj"] = calc

        except Exception as e:
            st.error(f"❌ 計算錯誤：{e}")
            import traceback
            st.code(traceback.format_exc())

if "result" in st.session_state:
    result = st.session_state["result"]
    calc   = st.session_state["calc_obj"]

# ─────────────────────────────────────────────────────────────
# Tab 2：VaR 計算結果
# ─────────────────────────────────────────────────────────────

with tab2:
    if result is None:
        st.info("📌 請先在「資料匯入 & 部位設定」頁面執行計算")
    else:
        base_ccy = result.base_currency

        # ── 多市場幣別說明 ──
        if result.asset_currencies:
            markets_str = "  |  ".join(
                f"{n}（{c}）" for n, c in result.asset_currencies.items()
            )
            fx_str = "  |  ".join(
                f"1 {c} = {r:.4f} {base_ccy}"
                for c, r in result.fx_rates_used.items()
                if c != base_ccy
            )
            if fx_str:
                st.markdown(f"""
<div class="info-box">
所有 VaR / CVaR 數值統一以 <b>{base_ccy}</b> 計。外幣換算匯率：{fx_str}
</div>
""", unsafe_allow_html=True)

        # ── 主要指標 ──
        st.markdown('<div class="section-header">📊 組合風險指標</div>', unsafe_allow_html=True)

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.markdown(f"""
<div class="metric-box">
  <div class="metric-label">VaR（{result.confidence*100:.1f}%，{result.horizon}日，{base_ccy}）</div>
  <div class="metric-value">{result.portfolio_var:,.0f}</div>
  <div style="font-size:0.75rem;color:#888">組合市值：{result.portfolio_value:,.0f} {base_ccy}</div>
</div>""", unsafe_allow_html=True)
        with c2:
            st.markdown(f"""
<div class="metric-box">
  <div class="metric-label">CVaR / Expected Shortfall（{base_ccy}）</div>
  <div class="metric-value">{result.portfolio_cvar:,.0f}</div>
  <div style="font-size:0.75rem;color:#888">超過 VaR 後的平均損失</div>
</div>""", unsafe_allow_html=True)
        with c3:
            var_pct = (result.portfolio_var / result.portfolio_value * 100
                       if result.portfolio_value > 0 else 0)
            st.markdown(f"""
<div class="metric-box">
  <div class="metric-label">VaR（% of 組合市值）</div>
  <div class="metric-value">{var_pct:.2f}%</div>
  <div style="font-size:0.75rem;color:#888">方法：{result.method}</div>
</div>""", unsafe_allow_html=True)
        with c4:
            st.markdown(f"""
<div class="metric-box">
  <div class="metric-label">分散化比率</div>
  <div class="metric-value-blue">{result.diversification_ratio*100:.1f}%</div>
  <div style="font-size:0.75rem;color:#888">越高表示分散效果越好</div>
</div>""", unsafe_allow_html=True)

        st.markdown("")

        # ── Component VaR 表格 ──
        st.markdown('<div class="section-header">📋 個別資產 VaR 分解（{base_ccy}）</div>'.replace(
            "{base_ccy}", base_ccy), unsafe_allow_html=True)

        comp_data = []
        for name in result.component_var:
            mv_base   = result.weights[name] * result.portfolio_value
            ccy       = result.asset_currencies.get(name, base_ccy)
            fx_rate   = result.fx_rates_used.get(ccy, 1.0)
            local_mv  = result.local_market_values.get(name, mv_base / fx_rate)
            cv        = result.component_var[name]
            ccv       = result.component_cvar[name]
            contrib   = cv / result.portfolio_var * 100 if result.portfolio_var > 0 else 0
            marginal  = result.marginal_var.get(name, 0)
            comp_data.append({
                "標的": name,
                "市場": get_market_label(name),
                "幣別": ccy,
                f"本地市值({ccy})": f"{local_mv:+,.0f}",
                f"TWD 市值": f"{mv_base:+,.0f}",
                "Component VaR": f"{cv:,.0f}",
                "Component CVaR": f"{ccv:,.0f}",
                "風險貢獻度": f"{contrib:.1f}%",
                "邊際 VaR（/unit）": f"{marginal:.4f}",
            })

        comp_df = pd.DataFrame(comp_data)
        st.dataframe(comp_df, use_container_width=True, hide_index=True)

        # ── 風險貢獻度圓餅圖（依市場分組）──
        col_pie, col_info = st.columns([1, 1])
        with col_pie:
            comp_vals  = [abs(result.component_var[n]) for n in result.component_var]
            comp_names = [
                f"{n}（{result.asset_currencies.get(n, base_ccy)}）"
                for n in result.component_var
            ]
            fig_pie = go.Figure(go.Pie(
                labels=comp_names,
                values=comp_vals,
                hole=0.4,
                textinfo="label+percent",
                marker=dict(colors=px.colors.qualitative.Set2),
            ))
            fig_pie.update_layout(
                title=f"風險貢獻度分解（VaR {result.confidence*100:.0f}%）",
                height=360,
                margin=dict(t=40, b=10, l=10, r=10),
                legend=dict(orientation="h", yanchor="bottom", y=-0.3),
            )
            st.plotly_chart(fig_pie, use_container_width=True)

        with col_info:
            st.markdown('<div class="section-header">📌 參數摘要</div>', unsafe_allow_html=True)
            info = {
                "計算方法": result.method,
                "信賴水準": f"{result.confidence*100:.1f}%",
                "持有天數": f"{result.horizon} 天",
                "歷史回溯": f"{len(result.returns_df)} 個交易日",
                "基礎幣別": base_ccy,
                "組合市值": f"{result.portfolio_value:,.0f} {base_ccy}",
                f"組合損益標準差（{result.horizon}日）": f"{result.portfolio_pnl_std:,.2f}",
                "VaR": f"{result.portfolio_var:,.2f} {base_ccy}",
                "CVaR (ES)": f"{result.portfolio_cvar:,.2f} {base_ccy}",
                "分散化節省": f"{max(0, sum(abs(v) for v in result.component_var.values()) - result.portfolio_var):,.2f}",
                "分散化比率": f"{result.diversification_ratio*100:.2f}%",
            }
            for k, v in info.items():
                col_k, col_v = st.columns([2, 1])
                col_k.markdown(f"<span style='font-size:0.85rem;color:#555'>{k}</span>",
                                unsafe_allow_html=True)
                col_v.markdown(
                    f"<span style='font-size:0.85rem;font-weight:700;color:#1F497D'>{v}</span>",
                    unsafe_allow_html=True,
                )

        # ── 下載報告 ──
        st.divider()
        st.markdown('<div class="section-header">⬇️ 匯出報告</div>', unsafe_allow_html=True)
        report_buf = io.BytesIO()
        with pd.ExcelWriter(report_buf, engine="openpyxl") as writer:
            comp_df.to_excel(writer, sheet_name="VaR分解", index=False)
            pd.DataFrame({
                "指標": list(info.keys()),
                "數值": list(info.values()),
            }).to_excel(writer, sheet_name="摘要", index=False)
            result.returns_df.to_excel(writer, sheet_name="報酬率序列")
            # FX 匯率表
            fx_df = pd.DataFrame([
                {"幣別": c, f"即期匯率（1{c}={base_ccy}）": r}
                for c, r in result.fx_rates_used.items()
            ])
            fx_df.to_excel(writer, sheet_name="匯率", index=False)

        st.download_button(
            "⬇️ 下載 Excel 報告",
            data=report_buf.getvalue(),
            file_name=f"VaR_Report_{result.method[:4]}_CI{int(result.confidence*100)}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

# ─────────────────────────────────────────────────────────────
# Tab 3：圖表分析
# ─────────────────────────────────────────────────────────────

with tab3:
    if result is None:
        st.info("📌 請先執行計算")
    else:
        base_ccy = result.base_currency

        # ── P&L 分布圖 ──
        st.markdown('<div class="section-header">📉 損益分布（P&L Distribution）</div>',
                    unsafe_allow_html=True)

        pnl = result.pnl_series
        var_line  = -result.portfolio_var
        cvar_line = -result.portfolio_cvar

        fig_hist = go.Figure()
        fig_hist.add_trace(go.Histogram(
            x=pnl, nbinsx=80, name="P&L 分布",
            marker_color="steelblue", opacity=0.75,
        ))
        fig_hist.add_vline(
            x=var_line, line_dash="dash", line_color="red",
            annotation_text=f"VaR ({result.confidence*100:.0f}%) = {result.portfolio_var:,.0f} {base_ccy}",
            annotation_position="top right",
        )
        fig_hist.add_vline(
            x=cvar_line, line_dash="dot", line_color="darkred",
            annotation_text=f"CVaR = {result.portfolio_cvar:,.0f} {base_ccy}",
            annotation_position="top left",
        )
        fig_hist.update_layout(
            title=f"組合 P&L 分布（{result.method}，{result.horizon}日，{base_ccy}）",
            xaxis_title=f"損益（{base_ccy}）",
            yaxis_title="頻率",
            height=380, bargap=0.05,
            legend=dict(orientation="h", y=-0.2),
        )
        st.plotly_chart(fig_hist, use_container_width=True)

        col3a, col3b = st.columns(2)

        # ── 歷史價格走勢（各市場正規化）──
        with col3a:
            st.markdown('<div class="section-header">📈 歷史價格（各市場正規化至 100）</div>',
                        unsafe_allow_html=True)
            prices_norm = (st.session_state["prices_df"]
                           / st.session_state["prices_df"].iloc[0] * 100)
            fig_price = go.Figure()
            # 依市場分色
            color_map = {"TWD": "steelblue", "USD": "coral", "KRW": "seagreen",
                         "HKD": "gold", "JPY": "purple"}
            for col in prices_norm.columns:
                ccy = detect_asset_currency(col)
                fig_price.add_trace(go.Scatter(
                    x=prices_norm.index, y=prices_norm[col],
                    name=f"{col}（{ccy}）", mode="lines",
                    line=dict(color=color_map.get(ccy, "gray")),
                ))
            fig_price.update_layout(
                height=320, xaxis_title="日期",
                yaxis_title="正規化價格（基準=100）",
                legend=dict(orientation="h", y=-0.3),
            )
            st.plotly_chart(fig_price, use_container_width=True)

        # ── 相關性熱圖（FX 調整後報酬）──
        with col3b:
            st.markdown(
                '<div class="section-header">🌡️ 報酬相關性矩陣（FX 調整後）</div>',
                unsafe_allow_html=True,
            )
            corr_mat = result.returns_df.corr()
            fig_heat = px.imshow(
                corr_mat, text_auto=".2f",
                color_continuous_scale="RdBu_r",
                zmin=-1, zmax=1, aspect="auto",
            )
            fig_heat.update_layout(height=320, coloraxis_showscale=False)
            st.plotly_chart(fig_heat, use_container_width=True)

        # ── 市場別 VaR 貢獻度（新增：依市場分組）──
        st.markdown('<div class="section-header">🌏 市場別風險貢獻度</div>',
                    unsafe_allow_html=True)
        market_var = {}
        for name, cv in result.component_var.items():
            mkt = get_market_label(name)
            market_var[mkt] = market_var.get(mkt, 0) + abs(cv)

        fig_mkt = go.Figure(go.Bar(
            x=list(market_var.keys()),
            y=list(market_var.values()),
            marker_color=["steelblue", "coral", "seagreen", "gold", "purple"][:len(market_var)],
            text=[f"{v:,.0f} {base_ccy}" for v in market_var.values()],
            textposition="auto",
        ))
        fig_mkt.update_layout(
            title=f"市場別 Component VaR 加總（{base_ccy}）",
            yaxis_title=f"VaR（{base_ccy}）", height=300,
        )
        st.plotly_chart(fig_mkt, use_container_width=True)

        # ── 滾動 VaR ──
        st.markdown('<div class="section-header">📊 滾動 VaR（252日窗口）</div>',
                    unsafe_allow_html=True)
        returns_all = result.returns_df
        w = np.array([calc.market_values[n] for n in result.component_var])
        pnl_ts = (returns_all * w).sum(axis=1)
        win = min(252, len(pnl_ts) - 20)
        rolling_var = pnl_ts.rolling(win).apply(
            lambda x: -np.percentile(x, (1 - result.confidence) * 100)
        )
        fig_roll = make_subplots(rows=2, cols=1, shared_xaxes=True,
                                  row_heights=[0.6, 0.4], vertical_spacing=0.05)
        fig_roll.add_trace(
            go.Scatter(x=pnl_ts.index, y=pnl_ts.values,
                       name=f"日損益（{base_ccy}）",
                       line=dict(color="steelblue", width=1)),
            row=1, col=1,
        )
        fig_roll.add_trace(
            go.Scatter(x=rolling_var.index, y=-rolling_var.values,
                       name=f"VaR({int(result.confidence*100)}%)",
                       line=dict(color="red", dash="dash", width=1.5)),
            row=1, col=1,
        )
        fig_roll.add_trace(
            go.Scatter(x=rolling_var.index, y=rolling_var.values,
                       fill="tozeroy", name="滾動VaR",
                       line=dict(color="red", width=1)),
            row=2, col=1,
        )
        fig_roll.update_layout(
            height=420, showlegend=True,
            yaxis_title=f"P&L（{base_ccy}）",
            yaxis2_title=f"VaR（{base_ccy}）",
        )
        st.plotly_chart(fig_roll, use_container_width=True)

# ─────────────────────────────────────────────────────────────
# Tab 4：回測 & 壓力測試
# ─────────────────────────────────────────────────────────────

with tab4:
    if result is None:
        st.info("📌 請先執行計算")
    else:
        base_ccy = result.base_currency

        # ── Kupiec 回測 ──
        st.markdown('<div class="section-header">🔍 Kupiec POF 回測</div>',
                    unsafe_allow_html=True)
        st.markdown("""
**Kupiec（1995）比例失敗測試**：
- $H_0$：例外發生率 $p = 1 - \\alpha$（模型正確）
- 使用概似比（LR）統計量，服從 $\\chi^2(1)$ 分布
""")

        returns_all = result.returns_df
        w_arr = np.array([calc.market_values[n] for n in result.component_var])
        pnl_ts = (returns_all * w_arr).sum(axis=1)
        rolling_var_bt = pnl_ts.rolling(
            min(252, len(pnl_ts) - 10)
        ).apply(lambda x: -np.percentile(x, (1 - result.confidence) * 100)).dropna()

        aligned_pnl = pnl_ts[rolling_var_bt.index]
        bt_result = calc.backtest_kupiec(rolling_var_bt, aligned_pnl, result.confidence)

        c_bt1, c_bt2, c_bt3, c_bt4 = st.columns(4)
        c_bt1.metric("樣本天數",     f"{bt_result['n_observations']} 天")
        c_bt2.metric("實際例外次數", f"{bt_result['n_exceptions']} 次")
        c_bt3.metric("實際例外率",   f"{bt_result['exception_rate']*100:.2f}%",
                      delta=f"預期 {bt_result['expected_rate']*100:.1f}%",
                      delta_color="inverse")
        c_bt4.metric("LR 統計量",    f"{bt_result['lr_statistic']:.4f}")

        if bt_result["reject_H0"]:
            st.error(f"**{bt_result['verdict']}** (p-value = {bt_result['p_value']:.4f})")
        else:
            st.success(f"**{bt_result['verdict']}** (p-value = {bt_result['p_value']:.4f})")

        # ── 例外日標示圖 ──
        st.markdown('<div class="section-header">📉 例外日（Exception Days）</div>',
                    unsafe_allow_html=True)
        exceptions_mask = aligned_pnl < -rolling_var_bt
        fig_bt = go.Figure()
        fig_bt.add_trace(go.Scatter(
            x=aligned_pnl.index, y=aligned_pnl.values,
            name=f"實際日損益（{base_ccy}）",
            line=dict(color="steelblue", width=1),
        ))
        fig_bt.add_trace(go.Scatter(
            x=rolling_var_bt.index, y=-rolling_var_bt.values,
            name=f"VaR（{result.confidence*100:.0f}%）",
            line=dict(color="red", dash="dash", width=1.5),
        ))
        exc_pnl = aligned_pnl[exceptions_mask]
        fig_bt.add_trace(go.Scatter(
            x=exc_pnl.index, y=exc_pnl.values,
            mode="markers", name="例外日",
            marker=dict(color="darkred", size=8, symbol="x"),
        ))
        fig_bt.update_layout(
            height=380, xaxis_title="日期",
            yaxis_title=f"損益（{base_ccy}）",
            legend=dict(orientation="h", y=-0.25),
        )
        st.plotly_chart(fig_bt, use_container_width=True)

        # ── 壓力測試 ──
        st.markdown(
            '<div class="section-header">⚡ 情境壓力測試（含幣別）</div>',
            unsafe_allow_html=True,
        )
        st.markdown("設定各資產的本地幣別衝擊幅度，以及外幣相對台幣的升貶幅度：")

        # 股票衝擊
        st.markdown("**股票本地價格衝擊（%）**")
        shock_data = {}
        n_cols = min(len(result.component_var), 4)
        cols_shock = st.columns(n_cols)
        for i, name in enumerate(result.component_var.keys()):
            ccy = result.asset_currencies.get(name, base_ccy)
            with cols_shock[i % n_cols]:
                shock = st.number_input(
                    f"{name}\n（{ccy}，%）",
                    value=-10.0, min_value=-100.0, max_value=100.0,
                    step=1.0, format="%.1f", key=f"shock_{name}",
                )
                shock_data[name] = shock / 100.0

        # FX 衝擊（外幣資產才顯示）
        foreign_ccys = list({
            c for c in result.asset_currencies.values() if c != base_ccy
        })
        fx_shock = {}
        if foreign_ccys:
            st.markdown("**外幣升貶幅度（相對 TWD，正數=外幣升值）**")
            cols_fx = st.columns(min(len(foreign_ccys), 4))
            for i, ccy in enumerate(foreign_ccys):
                with cols_fx[i % len(cols_fx)]:
                    s = st.number_input(
                        f"{ccy}/TWD 變動（%）",
                        value=0.0, min_value=-30.0, max_value=30.0,
                        step=0.5, format="%.1f", key=f"fx_shock_{ccy}",
                    )
                    fx_shock[ccy] = s / 100.0

        # 計算情境損益（股票衝擊 + FX 衝擊）
        scenario_pnl = 0.0
        for name, s in shock_data.items():
            ccy = result.asset_currencies.get(name, base_ccy)
            base_mv = calc.market_values[name]
            # 本地衝擊
            scenario_pnl += base_mv * s
            # FX 衝擊（對整體外幣市值的匯率變動）
            if ccy in fx_shock:
                scenario_pnl += base_mv * fx_shock[ccy]

        st.markdown("")
        col_s1, col_s2, col_s3 = st.columns(3)
        col_s1.metric(
            f"情境組合損益（{base_ccy}）",
            f"{scenario_pnl:+,.0f}",
            delta=f"{scenario_pnl/result.portfolio_value*100:+.2f}% of 市值",
            delta_color="normal",
        )
        col_s2.metric(
            "相較 VaR（倍數）",
            f"{abs(scenario_pnl)/result.portfolio_var:.2f}x VaR"
            if result.portfolio_var > 0 else "N/A",
        )
        col_s3.metric(
            f"組合市值（{base_ccy}）",
            f"{result.portfolio_value:,.0f}",
        )

        # ── 三方法比較 ──
        st.markdown(
            '<div class="section-header">🔄 三方法 VaR 比較</div>',
            unsafe_allow_html=True,
        )
        if st.button("執行三方法比較"):
            with st.spinner("比較計算中..."):
                try:
                    hs  = calc.historical(result.confidence, result.horizon)
                    par = calc.parametric(result.confidence, result.horizon)
                    mc  = calc.monte_carlo(result.confidence, result.horizon, n_sims=5000)

                    compare = pd.DataFrame({
                        "方法": ["Historical Simulation", "Parametric (Delta-Normal)", "Monte Carlo"],
                        f"VaR（{base_ccy}）":  [f"{hs.portfolio_var:,.2f}",
                                                 f"{par.portfolio_var:,.2f}",
                                                 f"{mc.portfolio_var:,.2f}"],
                        f"CVaR（{base_ccy}）": [f"{hs.portfolio_cvar:,.2f}",
                                                  f"{par.portfolio_cvar:,.2f}",
                                                  f"{mc.portfolio_cvar:,.2f}"],
                        "分散化比率": [f"{hs.diversification_ratio*100:.2f}%",
                                       f"{par.diversification_ratio*100:.2f}%",
                                       f"{mc.diversification_ratio*100:.2f}%"],
                    })
                    st.dataframe(compare, use_container_width=True, hide_index=True)

                    fig_compare = go.Figure(go.Bar(
                        x=["HS", "Parametric", "Monte Carlo"],
                        y=[hs.portfolio_var, par.portfolio_var, mc.portfolio_var],
                        marker_color=["steelblue", "coral", "seagreen"],
                        text=[f"{v:,.0f}" for v in [hs.portfolio_var, par.portfolio_var, mc.portfolio_var]],
                        textposition="auto",
                    ))
                    fig_compare.update_layout(
                        title=f"三方法 VaR 比較（CI={result.confidence*100:.0f}%，{result.horizon}日，{base_ccy}）",
                        yaxis_title=f"VaR（{base_ccy}）", height=320,
                    )
                    st.plotly_chart(fig_compare, use_container_width=True)
                except Exception as e:
                    st.error(f"比較計算失敗：{e}")
