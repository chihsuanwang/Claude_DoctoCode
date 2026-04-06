"""
app.py — CCDRA 評價系統主程式（Tkinter UI）
============================================
可贖回 CMS 連結每日區間計息票據評價工具

四個主要頁籤：
  Tab 1 - 產品條款     (Product Terms)
  Tab 2 - 市場資料     (Market Data)
  Tab 3 - 模型與模擬   (Model & Simulation)
  Tab 4 - 評價結果     (Results)

設計原則：
- UI 層與定價層完全解耦，僅透過 CCDRAPricer 介面互動
- 所有計算在獨立執行緒中進行，不凍結 UI
- 結果圖表使用 matplotlib 內嵌於 tkinter
"""

import sys
import os
import threading
import queue
from datetime import date, datetime
from typing import Dict, List, Optional
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import tkinter.font as tkfont

import numpy as np
import matplotlib
matplotlib.use('TkAgg')
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.ticker as mticker

# 確保可以找到 src 模組
sys.path.insert(0, os.path.dirname(__file__))
from src.market_data import MarketData, SwapCurvePoint, SwaptionVolPoint
from src.product import CCDRAProduct
from src.pricer import CCDRAPricer, PricingResult


# ─────────────────────────────────────────────────────────────
#  樣式常數
# ─────────────────────────────────────────────────────────────

BG_DARK    = '#1e1e2e'
BG_MID     = '#2a2a3e'
BG_LIGHT   = '#313250'
FG_WHITE   = '#cdd6f4'
FG_ACCENT  = '#89b4fa'   # 藍色強調
FG_GREEN   = '#a6e3a1'
FG_YELLOW  = '#f9e2af'
FG_RED     = '#f38ba8'
FG_PURPLE  = '#cba6f7'
FONT_MAIN  = ('Segoe UI', 10)
FONT_BOLD  = ('Segoe UI', 10, 'bold')
FONT_TITLE = ('Segoe UI', 12, 'bold')
FONT_MONO  = ('Consolas', 10)
PAD        = 8


# ─────────────────────────────────────────────────────────────
#  通用小工具
# ─────────────────────────────────────────────────────────────

def _lf(parent, text='', **kw) -> ttk.LabelFrame:
    kw.setdefault('padding', PAD)
    return ttk.LabelFrame(parent, text=text, **kw)


def _lbl(parent, text='', fg=None, **kw) -> tk.Label:
    kw.setdefault('bg', BG_MID)
    kw.setdefault('fg', fg or FG_WHITE)
    kw.setdefault('font', FONT_MAIN)
    return tk.Label(parent, text=text, **kw)


def _entry(parent, width=14, **kw) -> ttk.Entry:
    return ttk.Entry(parent, width=width, **kw)


def _make_tooltip(widget, text: str):
    tip = tk.Toplevel(widget)
    tip.withdraw()
    tip.overrideredirect(True)
    lbl = tk.Label(tip, text=text, bg='#ffffe0', relief='solid',
                   borderwidth=1, font=('Segoe UI', 9), padx=4, pady=2)
    lbl.pack()

    def enter(e):
        tip.geometry(f'+{e.x_root+12}+{e.y_root+12}')
        tip.deiconify()

    def leave(e):
        tip.withdraw()

    widget.bind('<Enter>', enter)
    widget.bind('<Leave>', leave)


# ─────────────────────────────────────────────────────────────
#  Tab 1：產品條款
# ─────────────────────────────────────────────────────────────

class ProductTab(ttk.Frame):
    """產品合約條款輸入頁籤。"""

    FIELDS = [
        # (label, key, default, tooltip)
        ('評價日 (YYYY-MM-DD)',    'pricing_date',   '2022-03-31', '市場資料對應日期'),
        ('生效日 (YYYY-MM-DD)',    'effective_date', '2022-03-31', '票據發行生效日'),
        ('到期日 (YYYY-MM-DD)',    'maturity_date',  '2027-03-31', '票據到期日'),
        ('名目本金 (歸一化)',       'nominal',        '100',        '通常為 100 (par)'),
        ('票面利率 (%)',           'coupon_rate',    '1.65',       '年化票面利率'),
        ('區間下限 (%)',           'floor_rate',     '0.00',       'CMS 在區間下限（含）'),
        ('區間上限 (%)',           'ceiling_rate',   '4.25',       'CMS 在區間上限（含）'),
        ('贖回價格',               'call_price',     '100',        '通常為 100 (par)'),
        ('封閉期 (年)',            'freeze_years',   '1',          '封閉期內不可被發行人贖回'),
        ('每年付息次數',           'payment_freq',   '4',          '4=季付, 2=半年付, 1=年付'),
        ('CMS 指標年期 (年)',      'cms_tenor',      '2',          '2→2Y USD CMS (USSW2)'),
        ('信用利差 (bps)',         'credit_spread',  '50',         '折現加碼（信用風險）'),
        ('最近 CMS 觀察值 (%)',    'last_fixing',    '2.55',       '最近一次 CMS 利率固定值'),
        ('已在區間天數',           'in_days',        '0',          '當前計息期已確認在區間的天數'),
    ]

    def __init__(self, parent):
        super().__init__(parent)
        self.vars: Dict[str, tk.StringVar] = {}
        self._build()

    def _build(self):
        self.configure(style='Dark.TFrame')
        canvas = tk.Canvas(self, bg=BG_DARK, highlightthickness=0)
        scroll = ttk.Scrollbar(self, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)

        inner = tk.Frame(canvas, bg=BG_DARK)
        canvas.create_window((0, 0), window=inner, anchor='nw')
        inner.bind('<Configure>', lambda e: canvas.configure(
            scrollregion=canvas.bbox('all')))

        # 標題
        tk.Label(inner, text='📋  CCDRA 產品條款輸入', bg=BG_DARK,
                 fg=FG_ACCENT, font=FONT_TITLE, pady=8).grid(
            row=0, column=0, columnspan=3, sticky='w', padx=PAD)

        # 欄位
        for row, (label, key, default, tip) in enumerate(self.FIELDS, start=1):
            var = tk.StringVar(value=default)
            self.vars[key] = var

            lbl = tk.Label(inner, text=label, bg=BG_DARK, fg=FG_WHITE,
                           font=FONT_MAIN, anchor='e', width=22)
            lbl.grid(row=row, column=0, padx=(PAD, 4), pady=4, sticky='e')

            ent = ttk.Entry(inner, textvariable=var, width=18,
                            font=FONT_MONO)
            ent.grid(row=row, column=1, padx=4, pady=4, sticky='w')

            tip_lbl = tk.Label(inner, text=f'ℹ  {tip}', bg=BG_DARK,
                               fg='#6c7086', font=('Segoe UI', 9))
            tip_lbl.grid(row=row, column=2, padx=(4, PAD), pady=4, sticky='w')

        # 載入範例按鈕
        row = len(self.FIELDS) + 1
        ttk.Button(inner, text='📥 載入文件範例參數',
                   command=self._load_example).grid(
            row=row, column=0, columnspan=2, padx=PAD, pady=(16, 4), sticky='w')

    def _load_example(self):
        ex = CCDRAProduct.get_example()
        md = MarketData.get_example_data()
        self.vars['pricing_date'].set(str(md.pricing_date))
        self.vars['effective_date'].set(str(ex.effective_date))
        self.vars['maturity_date'].set(str(ex.maturity_date))
        self.vars['nominal'].set(str(ex.nominal))
        self.vars['coupon_rate'].set(f'{ex.coupon_rate*100:.2f}')
        self.vars['floor_rate'].set(f'{ex.floor_rate*100:.2f}')
        self.vars['ceiling_rate'].set(f'{ex.ceiling_rate*100:.2f}')
        self.vars['call_price'].set(str(ex.call_price))
        self.vars['freeze_years'].set(str(ex.freeze_years))
        self.vars['payment_freq'].set(str(ex.payment_freq))
        self.vars['cms_tenor'].set(str(ex.cms_tenor_years))
        self.vars['credit_spread'].set(str(int(ex.credit_spread * 10000)))
        self.vars['last_fixing'].set(f'{ex.last_fixing*100:.2f}')
        self.vars['in_days'].set(str(ex.in_days))

    def get_product(self) -> CCDRAProduct:
        v = self.vars
        return CCDRAProduct(
            effective_date  = _parse_date(v['effective_date'].get()),
            maturity_date   = _parse_date(v['maturity_date'].get()),
            nominal         = float(v['nominal'].get()),
            coupon_rate     = float(v['coupon_rate'].get()) / 100,
            floor_rate      = float(v['floor_rate'].get()) / 100,
            ceiling_rate    = float(v['ceiling_rate'].get()) / 100,
            call_price      = float(v['call_price'].get()),
            freeze_years    = int(v['freeze_years'].get()),
            payment_freq    = int(v['payment_freq'].get()),
            cms_tenor_years = int(v['cms_tenor'].get()),
            credit_spread   = float(v['credit_spread'].get()) / 10000,
            last_fixing     = float(v['last_fixing'].get()) / 100,
            in_days         = int(v['in_days'].get()),
        )

    def get_pricing_date(self) -> date:
        return _parse_date(self.vars['pricing_date'].get())


# ─────────────────────────────────────────────────────────────
#  Tab 2：市場資料
# ─────────────────────────────────────────────────────────────

class MarketDataTab(ttk.Frame):
    """Swap 曲線 & Swaption 波動率矩陣輸入。"""

    DEFAULT_CURVE = [
        ('3M',  'CASH', '0.9616'),
        ('2Y',  'SWAP', '2.5534'),
        ('3Y',  'SWAP', '2.6531'),
        ('4Y',  'SWAP', '2.5978'),
        ('5Y',  'SWAP', '2.5229'),
        ('6Y',  'SWAP', '2.4800'),
        ('7Y',  'SWAP', '2.4523'),
        ('8Y',  'SWAP', '2.4300'),
        ('9Y',  'SWAP', '2.4144'),
        ('10Y', 'SWAP', '2.4065'),
        ('12Y', 'SWAP', '2.4040'),
        ('15Y', 'SWAP', '2.3997'),
        ('20Y', 'SWAP', '2.3809'),
        ('25Y', 'SWAP', '2.3185'),
        ('30Y', 'SWAP', '2.2529'),
    ]

    # Swaption Vol 矩陣 (expiry vs. tenor)
    VOL_EXPIRIES = [1, 2, 3, 5]
    VOL_TENORS   = [1, 2, 3, 5]
    DEFAULT_VOLS = [
        [47.0, 43.0, 40.0, 36.0],
        [41.0, 38.0, 36.0, 32.0],
        [37.0, 34.5, 32.5, 29.5],
        [33.0, 31.0, 29.5, 27.0],
    ]

    def __init__(self, parent):
        super().__init__(parent)
        self.curve_rows: List[List[ttk.Entry]] = []
        self.vol_cells: Dict[tuple, tk.StringVar] = {}
        self._build()

    def _build(self):
        self.configure(style='Dark.TFrame')
        pane = tk.PanedWindow(self, orient='horizontal',
                              bg=BG_DARK, sashwidth=6, sashrelief='groove')
        pane.pack(fill='both', expand=True)

        # ── 左：Swap 曲線 ──────────────────────
        left = tk.Frame(pane, bg=BG_DARK)
        pane.add(left, minsize=300)

        tk.Label(left, text='📈  Swap / Deposit 曲線 (%)', bg=BG_DARK,
                 fg=FG_ACCENT, font=FONT_TITLE, pady=6).pack(anchor='w', padx=PAD)

        # 表頭
        hdr = tk.Frame(left, bg=BG_MID)
        hdr.pack(fill='x', padx=PAD)
        for col, txt in enumerate(['期限', '類型', '利率 (%)'], start=0):
            tk.Label(hdr, text=txt, bg=BG_MID, fg=FG_YELLOW,
                     font=FONT_BOLD, width=9, anchor='center').grid(
                row=0, column=col, padx=2, pady=4)

        # 卷動框架
        cv_frame = tk.Frame(left, bg=BG_DARK)
        cv_frame.pack(fill='both', expand=True, padx=PAD)

        for i, (term, itype, rate) in enumerate(self.DEFAULT_CURVE):
            row_entries = []
            for j, val in enumerate([term, itype, rate]):
                e = ttk.Entry(cv_frame, width=10, font=FONT_MONO)
                e.insert(0, val)
                e.grid(row=i, column=j, padx=2, pady=1)
                row_entries.append(e)
            self.curve_rows.append(row_entries)

        # 載入範例按鈕
        ttk.Button(left, text='📥 還原預設曲線',
                   command=self._reset_curve).pack(padx=PAD, pady=8, anchor='w')

        # ── 右：Swaption Vol 矩陣 ──────────────
        right = tk.Frame(pane, bg=BG_DARK)
        pane.add(right, minsize=340)

        tk.Label(right, text='📊  Swaption 波動率矩陣 (%, Lognormal)',
                 bg=BG_DARK, fg=FG_ACCENT, font=FONT_TITLE, pady=6).pack(
            anchor='w', padx=PAD)
        tk.Label(right,
                 text='行 = 到期 (Expiry)，列 = 年期 (Tenor)',
                 bg=BG_DARK, fg='#6c7086', font=('Segoe UI', 9)).pack(
            anchor='w', padx=PAD)

        grid_frame = tk.Frame(right, bg=BG_DARK)
        grid_frame.pack(padx=PAD, pady=4)

        # 表頭列
        tk.Label(grid_frame, text='Exp↓\\Ten→', bg=BG_MID, fg=FG_YELLOW,
                 font=FONT_BOLD, width=10, relief='flat').grid(
            row=0, column=0, padx=2, pady=2)
        for j, ten in enumerate(self.VOL_TENORS):
            tk.Label(grid_frame, text=f'{ten}Y', bg=BG_MID, fg=FG_YELLOW,
                     font=FONT_BOLD, width=8, anchor='center').grid(
                row=0, column=j+1, padx=2, pady=2)

        for i, exp in enumerate(self.VOL_EXPIRIES):
            tk.Label(grid_frame, text=f'{exp}Y', bg=BG_MID, fg=FG_YELLOW,
                     font=FONT_BOLD, width=10, anchor='e').grid(
                row=i+1, column=0, padx=2, pady=2)
            for j, ten in enumerate(self.VOL_TENORS):
                var = tk.StringVar(value=str(self.DEFAULT_VOLS[i][j]))
                self.vol_cells[(exp, ten)] = var
                e = ttk.Entry(grid_frame, textvariable=var,
                              width=8, font=FONT_MONO)
                e.grid(row=i+1, column=j+1, padx=2, pady=2)

        ttk.Button(right, text='📥 還原預設波動率',
                   command=self._reset_vols).pack(padx=PAD, pady=8, anchor='w')

        # 備註
        tk.Label(right,
                 text='※ 用於 LMM ABCD 波動率模型校準',
                 bg=BG_DARK, fg='#6c7086', font=('Segoe UI', 9)).pack(
            anchor='w', padx=PAD)

    def _reset_curve(self):
        for i, (term, itype, rate) in enumerate(self.DEFAULT_CURVE):
            for j, val in enumerate([term, itype, rate]):
                self.curve_rows[i][j].delete(0, 'end')
                self.curve_rows[i][j].insert(0, val)

    def _reset_vols(self):
        for i, exp in enumerate(self.VOL_EXPIRIES):
            for j, ten in enumerate(self.VOL_TENORS):
                self.vol_cells[(exp, ten)].set(str(self.DEFAULT_VOLS[i][j]))

    def get_market_data(self, pricing_date: date) -> MarketData:
        md = MarketData(pricing_date=pricing_date)
        for row in self.curve_rows:
            term  = row[0].get().strip()
            itype = row[1].get().strip().upper()
            rate_pct = row[2].get().strip()
            if term and itype and rate_pct:
                try:
                    md.add_curve_point(term, itype, float(rate_pct) / 100)
                except Exception:
                    pass
        for (exp, ten), var in self.vol_cells.items():
            try:
                md.add_swaption_vol(exp, ten, float(var.get()) / 100)
            except Exception:
                pass
        return md


# ─────────────────────────────────────────────────────────────
#  Tab 3：模型參數
# ─────────────────────────────────────────────────────────────

class ModelTab(ttk.Frame):
    """LMM 模型參數與 Monte Carlo 設定頁籤。"""

    def __init__(self, parent):
        super().__init__(parent)
        self.vars: Dict[str, tk.StringVar] = {}
        self.calibrate_var = tk.BooleanVar(value=True)
        self._build()

    def _build(self):
        self.configure(style='Dark.TFrame')
        outer = tk.Frame(self, bg=BG_DARK)
        outer.pack(fill='both', expand=True, padx=PAD*2, pady=PAD*2)

        tk.Label(outer, text='⚙️  LMM 模型與模擬設定', bg=BG_DARK,
                 fg=FG_ACCENT, font=FONT_TITLE, pady=8).pack(anchor='w')

        # ── Monte Carlo 設定 ──
        mc_frame = tk.LabelFrame(outer, text=' Monte Carlo 模擬設定 ',
                                 bg=BG_DARK, fg=FG_YELLOW,
                                 font=FONT_BOLD, padx=PAD, pady=PAD)
        mc_frame.pack(fill='x', pady=(4, 8))

        mc_fields = [
            ('模擬路徑數',         'n_paths',          '5000',
             '建議 5000~20000，越多越準確但越慢'),
            ('每年模擬步驟數',     'n_steps_per_year', '12',
             '月頻=12，週頻=52（影響計算速度）'),
            ('隨機種子 (Seed)',    'seed',             '42',
             '固定種子可重現計算結果'),
        ]
        for row, (lbl, key, default, tip) in enumerate(mc_fields):
            var = tk.StringVar(value=default)
            self.vars[key] = var
            tk.Label(mc_frame, text=lbl, bg=BG_DARK, fg=FG_WHITE,
                     font=FONT_MAIN, width=20, anchor='e').grid(
                row=row, column=0, padx=4, pady=4, sticky='e')
            ttk.Entry(mc_frame, textvariable=var, width=12,
                      font=FONT_MONO).grid(
                row=row, column=1, padx=4, pady=4, sticky='w')
            tk.Label(mc_frame, text=tip, bg=BG_DARK, fg='#6c7086',
                     font=('Segoe UI', 9)).grid(
                row=row, column=2, padx=8, pady=4, sticky='w')

        # ── LMM 參數設定 ──
        lmm_frame = tk.LabelFrame(outer, text=' LMM ABCD 波動率 & 相關係數初始值 ',
                                  bg=BG_DARK, fg=FG_YELLOW,
                                  font=FONT_BOLD, padx=PAD, pady=PAD)
        lmm_frame.pack(fill='x', pady=(0, 8))

        ttk.Checkbutton(lmm_frame,
                        text='自動 Swaption 校準 LMM 參數（取消勾選則直接使用下方初始值）',
                        variable=self.calibrate_var).grid(
            row=0, column=0, columnspan=3, sticky='w', padx=4, pady=(0, 8))

        lmm_fields = [
            ('ABCD — a',   'lmm_a',    '0.30',  'ABCD 波動率函數參數'),
            ('ABCD — b',   'lmm_b',    '0.10',  ''),
            ('ABCD — c',   'lmm_c',    '0.50',  ''),
            ('ABCD — d',   'lmm_d',    '0.10',  ''),
            ('相關係數 ρ∞', 'lmm_rho',  '0.50',  '長期相關係數下限'),
            ('衰減率 β',   'lmm_beta', '0.10',  '指數衰減相關係數'),
        ]
        for row, (lbl, key, default, tip) in enumerate(lmm_fields, start=1):
            var = tk.StringVar(value=default)
            self.vars[key] = var
            tk.Label(lmm_frame, text=lbl, bg=BG_DARK, fg=FG_WHITE,
                     font=FONT_MAIN, width=20, anchor='e').grid(
                row=row, column=0, padx=4, pady=3, sticky='e')
            ttk.Entry(lmm_frame, textvariable=var, width=10,
                      font=FONT_MONO).grid(
                row=row, column=1, padx=4, pady=3, sticky='w')
            if tip:
                tk.Label(lmm_frame, text=tip, bg=BG_DARK, fg='#6c7086',
                         font=('Segoe UI', 9)).grid(
                    row=row, column=2, padx=8, pady=3, sticky='w')

        # ── 說明 ──
        note = (
            'ABCD 波動率函數（技術文件 p.10）：\n'
            '  σ_i(τ) = (a·τ + b)·exp(-c·τ) + d，  τ = T_i - t\n\n'
            '指數衰減相關係數（技術文件 p.10）：\n'
            '  ρ_{ij} = ρ∞ + (1-ρ∞)·exp(-β·|i-j|)'
        )
        tk.Label(outer, text=note, bg=BG_MID, fg='#89dceb',
                 font=FONT_MONO, justify='left', padx=12, pady=8,
                 relief='flat').pack(fill='x', pady=(4, 0))

    def get_params(self) -> dict:
        v = self.vars
        return {
            'n_paths':          int(v['n_paths'].get()),
            'n_steps_per_year': int(v['n_steps_per_year'].get()),
            'seed':             int(v['seed'].get()),
            'a':                float(v['lmm_a'].get()),
            'b':                float(v['lmm_b'].get()),
            'c':                float(v['lmm_c'].get()),
            'd':                float(v['lmm_d'].get()),
            'rho':              float(v['lmm_rho'].get()),
            'beta':             float(v['lmm_beta'].get()),
            'calibrate':        self.calibrate_var.get(),
        }


# ─────────────────────────────────────────────────────────────
#  Tab 4：評價結果
# ─────────────────────────────────────────────────────────────

class ResultsTab(ttk.Frame):
    """評價結果頁籤：數值摘要 + 圖表。"""

    def __init__(self, parent):
        super().__init__(parent)
        self._build()

    def _build(self):
        self.configure(style='Dark.TFrame')

        # 上半：摘要數值
        summary_frame = tk.Frame(self, bg=BG_DARK)
        summary_frame.pack(fill='x', padx=PAD, pady=(PAD, 4))

        tk.Label(summary_frame, text='📊  評價結果摘要', bg=BG_DARK,
                 fg=FG_ACCENT, font=FONT_TITLE, pady=6).pack(anchor='w')

        # 主要價格區塊
        price_frame = tk.Frame(summary_frame, bg=BG_MID, relief='flat',
                               borderwidth=2, padx=16, pady=12)
        price_frame.pack(fill='x', pady=4)

        self.price_labels = {}
        metrics = [
            ('CCDRA 理論價格\n（含贖回選擇權）', 'price',            FG_GREEN, 18),
            ('純 CDRA 價格\n（不含贖回）',       'price_no_call',    FG_ACCENT, 14),
            ('贖回選擇權價值',                   'call_option_value', FG_YELLOW, 14),
            ('預期贖回機率',                     'call_probability',  FG_PURPLE, 14),
        ]
        for col, (text, key, color, size) in enumerate(metrics):
            sub = tk.Frame(price_frame, bg=BG_MID)
            sub.grid(row=0, column=col, padx=20, pady=4)
            tk.Label(sub, text=text, bg=BG_MID, fg='#a6adc8',
                     font=('Segoe UI', 9), justify='center').pack()
            lbl = tk.Label(sub, text='—', bg=BG_MID, fg=color,
                           font=('Segoe UI', size, 'bold'))
            lbl.pack()
            self.price_labels[key] = lbl

        # 附加資訊
        info_frame = tk.Frame(summary_frame, bg=BG_DARK)
        info_frame.pack(fill='x', pady=(4, 0))
        self.info_vars = {
            'runtime':        tk.StringVar(value='—'),
            'n_paths':        tk.StringVar(value='—'),
            'cal_a':          tk.StringVar(value='—'),
            'cal_b':          tk.StringVar(value='—'),
            'cal_c':          tk.StringVar(value='—'),
            'cal_d':          tk.StringVar(value='—'),
            'cal_rho':        tk.StringVar(value='—'),
            'cal_beta':       tk.StringVar(value='—'),
        }
        info_items = [
            ('執行時間', 'runtime'), ('模擬路徑', 'n_paths'),
            ('校準 a', 'cal_a'), ('校準 b', 'cal_b'),
            ('校準 c', 'cal_c'), ('校準 d', 'cal_d'),
            ('校準 ρ∞', 'cal_rho'), ('校準 β', 'cal_beta'),
        ]
        for col, (lbl, key) in enumerate(info_items):
            sub = tk.Frame(info_frame, bg=BG_DARK)
            sub.grid(row=0, column=col, padx=8, pady=2)
            tk.Label(sub, text=lbl, bg=BG_DARK, fg='#6c7086',
                     font=('Segoe UI', 8)).pack()
            tk.Label(sub, textvariable=self.info_vars[key], bg=BG_DARK,
                     fg=FG_WHITE, font=FONT_MONO).pack()

        # 付息時程表
        sched_frame = tk.LabelFrame(self, text=' 付息時程明細 ',
                                    bg=BG_DARK, fg=FG_YELLOW,
                                    font=FONT_BOLD, padx=4, pady=4)
        sched_frame.pack(fill='x', padx=PAD, pady=4)

        cols = ('期次', '開始日', '結束日', '平均CMS%',
                'In-Range%', '期望票息', '折現因子', '現值', '可贖回')
        self.tree = ttk.Treeview(sched_frame, columns=cols,
                                 show='headings', height=8)
        widths = [40, 90, 90, 80, 80, 80, 80, 80, 60]
        for col, w in zip(cols, widths):
            self.tree.heading(col, text=col)
            self.tree.column(col, width=w, anchor='center')
        self.tree.pack(fill='x')

        # 圖表區
        chart_outer = tk.Frame(self, bg=BG_DARK)
        chart_outer.pack(fill='both', expand=True, padx=PAD, pady=4)
        self.chart_frame = chart_outer
        self.fig = None
        self.canvas_widget = None

    def display(self, result: PricingResult):
        """將 PricingResult 顯示到 UI。"""
        if result.error:
            messagebox.showerror('評價錯誤', result.error[:2000])
            return

        # 主要價格
        self.price_labels['price'].config(
            text=f'{result.price:.4f}')
        self.price_labels['price_no_call'].config(
            text=f'{result.price_no_call:.4f}')
        self.price_labels['call_option_value'].config(
            text=f'{result.call_option_value:.4f}')
        self.price_labels['call_probability'].config(
            text=f'{result.call_probability*100:.1f}%')

        # 附加資訊
        self.info_vars['runtime'].set(f'{result.runtime_sec:.1f} 秒')
        self.info_vars['n_paths'].set(f'{result.n_paths:,}')
        cp = result.calibrated_params
        for k in ('a', 'b', 'c', 'd', 'rho', 'beta'):
            key = f'cal_{k}'
            if k in cp:
                self.info_vars[key].set(f'{cp[k]:.4f}')

        # 付息時程表
        self.tree.delete(*self.tree.get_children())
        for s in result.coupon_schedule:
            tag = 'callable' if s['callable'] else 'locked'
            self.tree.insert('', 'end', values=(
                s['period'],
                s['start'][:10],
                s['end'][:10],
                f"{s['avg_cms_pct']:.2f}",
                f"{s['frac_in_range']:.1f}",
                f"{s['avg_coupon']:.4f}",
                f"{s['disc_factor']:.5f}",
                f"{s['pv']:.4f}",
                '✓' if s['callable'] else '—',
            ), tags=(tag,))
        self.tree.tag_configure('callable', foreground=FG_GREEN)
        self.tree.tag_configure('locked',   foreground='#6c7086')

        # 繪製圖表
        self._draw_charts(result)

    def _draw_charts(self, result: PricingResult):
        # 清除舊圖
        if self.canvas_widget:
            self.canvas_widget.get_tk_widget().destroy()

        fig, axes = plt.subplots(1, 3, figsize=(14, 3.6))
        fig.patch.set_facecolor(BG_DARK)

        # ── 子圖 1：CMS 利率路徑（前 50 條） ──
        ax1 = axes[0]
        ax1.set_facecolor(BG_MID)
        if result.cms_paths_sample is not None:
            cms = result.cms_paths_sample[:50] * 100
            n_t = cms.shape[1]
            t   = np.linspace(0, result.coupon_schedule[-1]['end'][:10]
                              if result.coupon_schedule else 5, n_t)
            for path in cms:
                ax1.plot(t, path, alpha=0.25, linewidth=0.6,
                         color='#89b4fa')
            ax1.axhline(result.coupon_schedule[0]['avg_cms_pct']
                        if result.coupon_schedule else 0,
                        color=FG_GREEN, linewidth=1.2,
                        linestyle='--', label='平均 CMS')
            # 區間帶
            prod_floor   = result.coupon_schedule[0]['avg_cms_pct'] * 0  # placeholder
            # 取第一條排程的 avg_cms 差估 floor/ceiling
        ax1.set_title('CMS 利率模擬路徑 (前50條)', color=FG_WHITE, fontsize=9)
        ax1.set_xlabel('時間 (年)', color='#6c7086', fontsize=8)
        ax1.set_ylabel('CMS (%)', color='#6c7086', fontsize=8)
        ax1.tick_params(colors='#6c7086', labelsize=7)
        for spine in ax1.spines.values():
            spine.set_edgecolor('#313250')

        # ── 子圖 2：每期在區間比例 ──
        ax2 = axes[1]
        ax2.set_facecolor(BG_MID)
        if result.coupon_schedule:
            periods = [s['period'] for s in result.coupon_schedule]
            fracs   = [s['frac_in_range'] for s in result.coupon_schedule]
            colors  = [FG_GREEN if s['callable'] else FG_ACCENT
                       for s in result.coupon_schedule]
            ax2.bar(periods, fracs, color=colors, edgecolor='#1e1e2e',
                    linewidth=0.5)
            ax2.axhline(50, color=FG_YELLOW, linewidth=1,
                        linestyle='--', alpha=0.7)
        ax2.set_title('每期 In-Range 比例 (%)', color=FG_WHITE, fontsize=9)
        ax2.set_xlabel('期次', color='#6c7086', fontsize=8)
        ax2.set_ylim(0, 105)
        ax2.tick_params(colors='#6c7086', labelsize=7)
        for spine in ax2.spines.values():
            spine.set_edgecolor('#313250')
        # 圖例
        from matplotlib.patches import Patch
        leg = [Patch(facecolor=FG_GREEN, label='可贖回期'),
               Patch(facecolor=FG_ACCENT, label='封閉期')]
        ax2.legend(handles=leg, fontsize=7, facecolor=BG_MID,
                   labelcolor=FG_WHITE, framealpha=0.8)

        # ── 子圖 3：現金流量貢獻 ──
        ax3 = axes[2]
        ax3.set_facecolor(BG_MID)
        if result.coupon_schedule:
            pvs  = [s['pv'] for s in result.coupon_schedule]
            ax3.bar(periods, pvs, color=FG_PURPLE,
                    edgecolor='#1e1e2e', linewidth=0.5)
            # 本金
            disc_last = result.coupon_schedule[-1]['disc_factor']
            ax3.bar([periods[-1] + 1], [100 * disc_last],
                    color=FG_YELLOW, edgecolor='#1e1e2e', label='本金現值')
        ax3.set_title('各期現金流量現值', color=FG_WHITE, fontsize=9)
        ax3.set_xlabel('期次', color='#6c7086', fontsize=8)
        ax3.tick_params(colors='#6c7086', labelsize=7)
        for spine in ax3.spines.values():
            spine.set_edgecolor('#313250')

        fig.tight_layout(pad=1.5)

        canvas = FigureCanvasTkAgg(fig, master=self.chart_frame)
        canvas.draw()
        canvas.get_tk_widget().pack(fill='both', expand=True)
        self.canvas_widget = canvas
        plt.close(fig)


# ─────────────────────────────────────────────────────────────
#  主應用程式視窗
# ─────────────────────────────────────────────────────────────

class CCDRAApp(tk.Tk):
    """CCDRA 評價系統主視窗。"""

    def __init__(self):
        super().__init__()
        self.title('CCDRA 評價系統  —  可贖回 CMS 連結每日區間計息票據  (QuantLib)')
        self.geometry('1280x820')
        self.configure(bg=BG_DARK)
        self._setup_styles()
        self._build()
        self._msg_queue: queue.Queue = queue.Queue()
        self._is_pricing = False

    def _setup_styles(self):
        style = ttk.Style(self)
        style.theme_use('clam')

        style.configure('.',
                        background=BG_DARK, foreground=FG_WHITE,
                        font=FONT_MAIN)
        style.configure('TFrame',        background=BG_DARK)
        style.configure('Dark.TFrame',   background=BG_DARK)
        style.configure('TLabelframe',   background=BG_DARK,
                        foreground=FG_YELLOW, bordercolor=BG_LIGHT)
        style.configure('TLabelframe.Label', background=BG_DARK,
                        foreground=FG_YELLOW)
        style.configure('TNotebook',     background=BG_DARK,
                        tabmargins=[2, 4, 2, 0])
        style.configure('TNotebook.Tab', background=BG_MID,
                        foreground=FG_WHITE, padding=[14, 6],
                        font=FONT_BOLD)
        style.map('TNotebook.Tab',
                  background=[('selected', BG_LIGHT)],
                  foreground=[('selected', FG_ACCENT)])
        style.configure('TEntry',        fieldbackground=BG_LIGHT,
                        foreground=FG_WHITE, insertcolor=FG_WHITE,
                        bordercolor=BG_MID, lightcolor=BG_MID,
                        darkcolor=BG_MID)
        style.configure('TButton',       background=FG_ACCENT,
                        foreground=BG_DARK, font=FONT_BOLD,
                        padding=[10, 5])
        style.map('TButton',
                  background=[('active', '#b9d4ff'), ('disabled', '#45475a')])
        style.configure('TCheckbutton',  background=BG_DARK,
                        foreground=FG_WHITE)
        style.configure('Treeview',
                        background=BG_MID, foreground=FG_WHITE,
                        fieldbackground=BG_MID,
                        rowheight=22, font=FONT_MONO)
        style.configure('Treeview.Heading',
                        background=BG_LIGHT, foreground=FG_YELLOW,
                        font=FONT_BOLD)
        style.map('Treeview', background=[('selected', BG_LIGHT)])
        style.configure('TScrollbar',    background=BG_MID,
                        troughcolor=BG_DARK, arrowcolor=FG_WHITE)
        style.configure('Horizontal.TProgressbar',
                        troughcolor=BG_MID, background=FG_ACCENT,
                        thickness=10)

    def _build(self):
        # ── 頂部標題列 ──────────────────────────
        header = tk.Frame(self, bg=BG_MID, pady=10)
        header.pack(fill='x')
        tk.Label(header,
                 text='可贖回 CMS 連結每日區間計息票據  評價系統',
                 bg=BG_MID, fg=FG_ACCENT,
                 font=('Segoe UI', 14, 'bold')).pack(side='left', padx=16)
        tk.Label(header,
                 text='QuantLib + LIBOR Market Model + LSMC',
                 bg=BG_MID, fg='#6c7086',
                 font=('Segoe UI', 10)).pack(side='left', padx=4)

        # ── Notebook ────────────────────────────
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill='both', expand=True, padx=4, pady=4)

        self.tab_product = ProductTab(self.nb)
        self.tab_market  = MarketDataTab(self.nb)
        self.tab_model   = ModelTab(self.nb)
        self.tab_results = ResultsTab(self.nb)

        self.nb.add(self.tab_product, text='  📋 產品條款  ')
        self.nb.add(self.tab_market,  text='  📈 市場資料  ')
        self.nb.add(self.tab_model,   text='  ⚙️  模型設定  ')
        self.nb.add(self.tab_results, text='  📊 評價結果  ')

        # ── 底部狀態列 ──────────────────────────
        footer = tk.Frame(self, bg=BG_MID, pady=6)
        footer.pack(fill='x', side='bottom')

        self.status_var = tk.StringVar(value='就緒。請輸入參數後按「開始評價」。')
        tk.Label(footer, textvariable=self.status_var,
                 bg=BG_MID, fg=FG_WHITE, font=FONT_MAIN).pack(
            side='left', padx=12)

        self.progress = ttk.Progressbar(footer, mode='determinate',
                                        length=260,
                                        style='Horizontal.TProgressbar')
        self.progress.pack(side='right', padx=12)

        self.btn_price = ttk.Button(footer, text='▶  開始評價',
                                    command=self._start_pricing)
        self.btn_price.pack(side='right', padx=4)

        ttk.Button(footer, text='💾 匯出結果',
                   command=self._export_results).pack(side='right', padx=4)

    # ── 評價流程 ──────────────────────────────────

    def _start_pricing(self):
        if self._is_pricing:
            return
        try:
            product      = self.tab_product.get_product()
            pricing_date = self.tab_product.get_pricing_date()
            market_data  = self.tab_market.get_market_data(pricing_date)
            model_params = self.tab_model.get_params()
        except Exception as e:
            messagebox.showerror('參數錯誤', str(e))
            return

        self._is_pricing = True
        self.btn_price.config(state='disabled', text='計算中…')
        self.progress['value'] = 0

        # 切換到結果頁籤
        self.nb.select(self.tab_results)

        # 在背景執行緒執行評價
        def run():
            def progress_cb(pct, msg):
                self._msg_queue.put(('progress', pct, msg))

            pricer = CCDRAPricer(
                product      = product,
                market_data  = market_data,
                n_paths          = model_params['n_paths'],
                n_steps_per_year = model_params['n_steps_per_year'],
                seed             = model_params['seed'],
                a                = model_params['a'],
                b                = model_params['b'],
                c                = model_params['c'],
                d                = model_params['d'],
                rho              = model_params['rho'],
                beta             = model_params['beta'],
                calibrate        = model_params['calibrate'],
            )
            result = pricer.price(progress_callback=progress_cb)
            self._msg_queue.put(('done', result))

        threading.Thread(target=run, daemon=True).start()
        self._poll_queue()

    def _poll_queue(self):
        try:
            while True:
                msg = self._msg_queue.get_nowait()
                if msg[0] == 'progress':
                    _, pct, text = msg
                    self.progress['value'] = pct
                    self.status_var.set(text)
                elif msg[0] == 'done':
                    result = msg[1]
                    self._is_pricing = False
                    self.btn_price.config(state='normal', text='▶  開始評價')
                    self.progress['value'] = 100
                    if result.error:
                        self.status_var.set('❌ 評價失敗（詳見錯誤訊息）')
                    else:
                        self.status_var.set(
                            f'✅ 完成！CCDRA 價格 = {result.price:.4f}  '
                            f'（{result.runtime_sec:.1f}秒，{result.n_paths:,}路徑）'
                        )
                    self.tab_results.display(result)
                    self._last_result = result
                    return
        except queue.Empty:
            pass
        if self._is_pricing:
            self.after(100, self._poll_queue)

    # ── 匯出 ───────────────────────────────────────

    def _export_results(self):
        if not hasattr(self, '_last_result') or self._last_result is None:
            messagebox.showinfo('提示', '請先執行評價。')
            return
        path = filedialog.asksaveasfilename(
            defaultextension='.txt',
            filetypes=[('文字檔', '*.txt'), ('所有檔案', '*.*')],
            title='儲存評價結果'
        )
        if not path:
            return
        result = self._last_result
        lines = [
            '=' * 60,
            '  CCDRA 評價結果報告',
            '=' * 60,
            f'  CCDRA 理論價格（含贖回）  : {result.price:.6f}',
            f'  純 CDRA 價格（不含贖回）  : {result.price_no_call:.6f}',
            f'  贖回選擇權價值            : {result.call_option_value:.6f}',
            f'  預期贖回機率              : {result.call_probability*100:.2f}%',
            f'  執行時間                  : {result.runtime_sec:.2f} 秒',
            f'  模擬路徑數                : {result.n_paths:,}',
            '',
            '  校準後 LMM 參數：',
        ]
        for k, v in result.calibrated_params.items():
            lines.append(f'    {k} = {v:.6f}')
        lines += ['', '  付息時程明細：',
                  f'  {"期次":>4} {"開始日":>12} {"結束日":>12} '
                  f'{"平均CMS%":>10} {"InRange%":>9} '
                  f'{"期望票息":>10} {"現值":>10}']
        for s in result.coupon_schedule:
            lines.append(
                f'  {s["period"]:>4} {s["start"][:10]:>12} {s["end"][:10]:>12} '
                f'{s["avg_cms_pct"]:>9.2f}% {s["frac_in_range"]:>8.1f}% '
                f'{s["avg_coupon"]:>10.4f} {s["pv"]:>10.4f}'
            )
        lines.append('=' * 60)

        with open(path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        messagebox.showinfo('完成', f'結果已儲存至：\n{path}')


# ─────────────────────────────────────────────────────────────
#  工具函式
# ─────────────────────────────────────────────────────────────

def _parse_date(s: str) -> date:
    for fmt in ('%Y-%m-%d', '%Y/%m/%d', '%Y%m%d'):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f'無法解析日期：{s}  （請使用 YYYY-MM-DD 格式）')


# ─────────────────────────────────────────────────────────────
#  進入點
# ─────────────────────────────────────────────────────────────

if __name__ == '__main__':
    app = CCDRAApp()
    app.mainloop()
