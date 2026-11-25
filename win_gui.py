#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
S.C.H.A.L.E. Course Suite — Windows Desktop GUI Client (Tkinter)
Starts and manages UOOC (51088), Zhihuishu (51089), and Chaoxing Multi-Account (51092)
locally with a Blue Archive styled control panel.
"""
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, scrolledtext
import webbrowser

from core.paths import get_data_dir
from server import SuiteManager


class SchaleDesktopApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("S.C.H.A.L.E. Course Suite // 蔚蓝档案网课助手桌面端")
        self.root.geometry("760x560")
        self.root.minsize(680, 500)
        self.root.configure(bg="#eaf5ff")

        self.manager = None
        self.running = False

        self.host_var = tk.StringVar(value="127.0.0.1")
        self.lan_var = tk.BooleanVar(value=False)
        self.uooc_port_var = tk.StringVar(value="51088")
        self.zhs_port_var = tk.StringVar(value="51089")
        self.cx_port_var = tk.StringVar(value="51092")

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Auto-start services on launch for zero-friction UX
        self.root.after(250, self.start_services)

    def _build_ui(self):
        # Header Banner (Blue Archive Cyan)
        header = tk.Frame(self.root, bg="#128afa", height=74)
        header.pack(fill="x")
        header.pack_propagate(False)

        title_box = tk.Frame(header, bg="#128afa")
        title_box.pack(side="left", padx=20, pady=12)
        tk.Label(
            title_box,
            text="S.C.H.A.L.E. // 蔚蓝档案多平台网课控制台",
            font=("Microsoft YaHei UI", 14, "bold"),
            fg="#ffffff",
            bg="#128afa",
        ).pack(anchor="w")
        tk.Label(
            title_box,
            text="超星学习通(多账号) · UOOC 优课在线 · 知道智慧树 · Windows 独立桌面端",
            font=("Microsoft YaHei UI", 9),
            fg="#dbeeff",
            bg="#128afa",
        ).pack(anchor="w")

        self.status_badge = tk.Label(
            header,
            text="● 启动中...",
            font=("Microsoft YaHei UI", 10, "bold"),
            fg="#ffffff",
            bg="#0b6bcb",
            padx=14,
            pady=6,
        )
        self.status_badge.pack(side="right", padx=20)

        body = tk.Frame(self.root, bg="#eaf5ff")
        body.pack(fill="both", expand=True, padx=18, pady=14)

        # Platform Cards Row
        cards_frame = tk.Frame(body, bg="#eaf5ff")
        cards_frame.pack(fill="x", pady=(0, 12))
        cards_frame.columnconfigure(0, weight=1)
        cards_frame.columnconfigure(1, weight=1)
        cards_frame.columnconfigure(2, weight=1)

        self._make_platform_card(
            cards_frame,
            col=0,
            title="超星学习通 (多账号)",
            character="阿罗娜 / 普拉娜",
            accent="#128afa",
            port_var=self.cx_port_var,
            key="cx",
        )
        self._make_platform_card(
            cards_frame,
            col=1,
            title="UOOC 优课在线",
            character="圣园未花 · Mika",
            accent="#ec4899",
            port_var=self.uooc_port_var,
            key="uooc",
        )
        self._make_platform_card(
            cards_frame,
            col=2,
            title="知道智慧树",
            character="才羽绿 & 才羽桃井",
            accent="#10b981",
            port_var=self.zhs_port_var,
            key="zhs",
        )

        # Control Bar
        ctrl = tk.Frame(body, bg="#ffffff", highlightbackground="#bae0ff", highlightthickness=1)
        ctrl.pack(fill="x", pady=(0, 12))

        ctrl_inner = tk.Frame(ctrl, bg="#ffffff")
        ctrl_inner.pack(fill="x", padx=14, pady=10)

        self.btn_start = tk.Button(
            ctrl_inner,
            text="▶ 启动全部服务",
            command=self.start_services,
            font=("Microsoft YaHei UI", 9, "bold"),
            bg="#128afa",
            fg="#ffffff",
            activebackground="#0b6bcb",
            activeforeground="#ffffff",
            relief="flat",
            padx=14,
            pady=6,
            cursor="hand2",
        )
        self.btn_start.pack(side="left", padx=(0, 8))

        self.btn_stop = tk.Button(
            ctrl_inner,
            text="⏹ 停止全部服务",
            command=self.stop_services,
            font=("Microsoft YaHei UI", 9, "bold"),
            bg="#ff5d8f",
            fg="#ffffff",
            activebackground="#e11d48",
            activeforeground="#ffffff",
            relief="flat",
            padx=14,
            pady=6,
            cursor="hand2",
            state="disabled",
        )
        self.btn_stop.pack(side="left", padx=(0, 12))

        tk.Checkbutton(
            ctrl_inner,
            text="允许局域网设备访问 (0.0.0.0)",
            variable=self.lan_var,
            bg="#ffffff",
            fg="#1e3a5f",
            font=("Microsoft YaHei UI", 9),
            activebackground="#ffffff",
        ).pack(side="left")

        tk.Button(
            ctrl_inner,
            text="📂 打开数据目录",
            command=self._open_data_dir,
            font=("Microsoft YaHei UI", 9),
            bg="#e0f2fe",
            fg="#0c4a6e",
            relief="flat",
            padx=10,
            pady=5,
            cursor="hand2",
        ).pack(side="right")

        # Log Console
        log_header = tk.Frame(body, bg="#eaf5ff")
        log_header.pack(fill="x", pady=(0, 4))
        tk.Label(
            log_header,
            text="SHITTIM CHEST // 运行日志",
            font=("Microsoft YaHei UI", 9, "bold"),
            fg="#1e3a5f",
            bg="#eaf5ff",
        ).pack(side="left")
        tk.Label(
            log_header,
            text=f"数据目录: {get_data_dir()}",
            font=("Consolas", 8),
            fg="#64748b",
            bg="#eaf5ff",
        ).pack(side="right")

        self.log_box = scrolledtext.ScrolledText(
            body,
            height=13,
            bg="#0f1b2e",
            fg="#dbeeff",
            font=("Consolas", 9),
            relief="flat",
            padx=10,
            pady=8,
            state="disabled",
        )
        self.log_box.pack(fill="both", expand=True)

    def _make_platform_card(self, parent, col, title, character, accent, port_var, key):
        card = tk.Frame(parent, bg="#ffffff", highlightbackground="#bae0ff", highlightthickness=1)
        pad_l = 0 if col == 0 else 6
        pad_r = 0 if col == 2 else 6
        card.grid(row=0, column=col, sticky="nsew", padx=(pad_l, pad_r))

        bar = tk.Frame(card, bg=accent, height=5)
        bar.pack(fill="x")

        inner = tk.Frame(card, bg="#ffffff")
        inner.pack(fill="both", expand=True, padx=12, pady=10)

        tk.Label(inner, text=title, font=("Microsoft YaHei UI", 10, "bold"), fg="#14284b", bg="#ffffff").pack(anchor="w")
        tk.Label(inner, text=f"主题角色：{character}", font=("Microsoft YaHei UI", 8), fg=accent, bg="#ffffff").pack(anchor="w", pady=(2, 8))

        port_row = tk.Frame(inner, bg="#ffffff")
        port_row.pack(fill="x", pady=(0, 8))
        tk.Label(port_row, text="端口:", font=("Microsoft YaHei UI", 8), fg="#4a6382", bg="#ffffff").pack(side="left")
        tk.Entry(port_row, textvariable=port_var, width=8, font=("Consolas", 9), relief="solid", bd=1).pack(side="left", padx=6)

        tk.Button(
            inner,
            text="🌐 打开控制台页面",
            command=lambda k=key, pv=port_var: self._open_url(pv.get()),
            font=("Microsoft YaHei UI", 9, "bold"),
            bg=accent,
            fg="#ffffff",
            activebackground=accent,
            activeforeground="#ffffff",
            relief="flat",
            pady=5,
            cursor="hand2",
        ).pack(fill="x")

    def log(self, msg: str):
        ts = time.strftime("%H:%M:%S")

        def _append():
            self.log_box.configure(state="normal")
            self.log_box.insert("end", f"[{ts}] {msg}\n")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")

        self.root.after(0, _append)

    def _open_url(self, port_str: str):
        try:
            p = int(port_str.strip())
            webbrowser.open(f"http://127.0.0.1:{p}")
        except Exception:
            messagebox.showwarning("端口无效", "请填写有效的数字端口号。")

    def _open_data_dir(self):
        path = str(get_data_dir())
        try:
            os.startfile(path)
        except Exception:
            webbrowser.open(f"file://{path}")

    def start_services(self):
        if self.running:
            return
        try:
            uooc_p = int(self.uooc_port_var.get().strip())
            zhs_p = int(self.zhs_port_var.get().strip())
            cx_p = int(self.cx_port_var.get().strip())
        except ValueError:
            messagebox.showerror("端口错误", "请确保三个端口号都是有效整数。")
            return

        host = "0.0.0.0" if self.lan_var.get() else "127.0.0.1"
        self.btn_start.configure(state="disabled")
        self.status_badge.configure(text="● 启动中...", bg="#f59e0b")
        self.log(f"正在启动三平台本地服务 (监听 {host})...")

        def _boot():
            mgr = SuiteManager(host=host, uooc_port=uooc_p, zhs_port=zhs_p, cx_port=cx_p)
            results = mgr.start_all(log_cb=self.log)
            self.manager = mgr
            ok_cnt = sum(1 for v in results.values() if v)
            self.running = ok_cnt > 0

            def _update_ui():
                if self.running:
                    self.status_badge.configure(text=f"● 运行中 ({ok_cnt}/3)", bg="#10b981")
                    self.btn_stop.configure(state="normal")
                    self.log("全部服务已就绪！点击上方任意角色卡片即可在浏览器打开控制台。")
                else:
                    self.status_badge.configure(text="● 启动失败", bg="#e11d48")
                    self.btn_start.configure(state="normal")

            self.root.after(0, _update_ui)

        threading.Thread(target=_boot, daemon=True).start()

    def stop_services(self):
        if not self.running or not self.manager:
            return
        self.btn_stop.configure(state="disabled")
        self.status_badge.configure(text="● 正在停止...", bg="#f59e0b")

        def _shutdown():
            self.manager.stop_all(log_cb=self.log)
            self.manager = None
            self.running = False

            def _ui():
                self.status_badge.configure(text="● 已停止", bg="#64748b")
                self.btn_start.configure(state="normal")

            self.root.after(0, _ui)

        threading.Thread(target=_shutdown, daemon=True).start()

    def _on_close(self):
        if self.running:
            if not messagebox.askokcancel("确认退出", "退出将停止本地运行的全部网课后台服务，确定退出吗？"):
                return
            if self.manager:
                self.manager.stop_all(log_cb=lambda m: None)
        self.root.destroy()


def main():
    root = tk.Tk()
    SchaleDesktopApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
