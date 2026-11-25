#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
S.C.H.A.L.E. Course Suite — Unified Cross-Platform Server (Linux / Windows / Docker)
Runs UOOC (Mika), Zhihuishu (Midori & Momoi), and Chaoxing Multi-Account (Arona & Plana)
inside a single process with graceful shutdown.
"""
import argparse
import logging
import os
import signal
import sys
import threading
import time
from typing import Dict, List, Optional

from werkzeug.serving import make_server

from core.paths import get_data_dir


class ServiceRunner:
    def __init__(self, key: str, label: str, host: str, port: int, app):
        self.key = key
        self.label = label
        self.host = host
        self.port = port
        self.app = app
        self.httpd = None
        self.thread: Optional[threading.Thread] = None
        self.running = False
        self.error = ""

    def start(self) -> bool:
        if self.running:
            return True
        try:
            self.httpd = make_server(self.host, self.port, self.app, threaded=True)
            self.running = True
            self.error = ""
            self.thread = threading.Thread(
                target=self._serve,
                name=f"srv-{self.key}",
                daemon=True,
            )
            self.thread.start()
            return True
        except Exception as e:
            self.error = str(e)
            self.running = False
            return False

    def _serve(self):
        try:
            self.httpd.serve_forever()
        except Exception as e:
            self.error = str(e)
        finally:
            self.running = False

    def stop(self):
        if self.httpd is not None:
            try:
                self.httpd.shutdown()
                self.httpd.server_close()
            except Exception:
                pass
            self.httpd = None
        self.running = False


class SuiteManager:
    def __init__(
        self,
        host: str = "0.0.0.0",
        uooc_port: int = 51088,
        zhs_port: int = 51089,
        cx_port: int = 51092,
        enabled: Optional[List[str]] = None,
    ):
        self.host = host
        self.ports = {"uooc": uooc_port, "zhs": zhs_port, "cx": cx_port}
        self.enabled = enabled or ["uooc", "zhs", "cx"]
        self.services: Dict[str, ServiceRunner] = {}

    def start_all(self, log_cb=print) -> Dict[str, bool]:
        results: Dict[str, bool] = {}
        logging.getLogger("werkzeug").setLevel(logging.WARNING)

        builders = {
            "uooc": ("UOOC 优课在线 (圣园未花)", lambda: __import__("platforms.uooc.uooc_app", fromlist=["create_app"]).create_app()),
            "zhs": ("知道智慧树 (才羽绿 & 才羽桃井)", lambda: __import__("platforms.zhs.zhs_app", fromlist=["create_app"]).create_app()),
            "cx": ("超星学习通多账号 (阿罗娜 / 普拉娜)", lambda: __import__("platforms.cx.cx_app", fromlist=["create_app"]).create_app()),
        }

        for key in self.enabled:
            if key not in builders:
                continue
            label, factory = builders[key]
            port = self.ports[key]
            try:
                app = factory()
                runner = ServiceRunner(key, label, self.host, port, app)
                ok = runner.start()
                self.services[key] = runner
                results[key] = ok
                if ok:
                    show_host = "127.0.0.1" if self.host == "0.0.0.0" else self.host
                    log_cb(f"[OK] {label} -> http://{show_host}:{port}")
                else:
                    log_cb(f"[ERR] {label} 启动失败 (端口 {port}): {runner.error}")
            except Exception as e:
                results[key] = False
                log_cb(f"[ERR] {label} 初始化失败: {e}")

        return results

    def stop_all(self, log_cb=print):
        for key, runner in list(self.services.items()):
            runner.stop()
            log_cb(f"[STOP] {runner.label} (:{runner.port}) 已停止")
        self.services.clear()


def main():
    parser = argparse.ArgumentParser(description="S.C.H.A.L.E. Course Suite — Unified Multi-Platform Course Server")
    parser.add_argument("--host", default=os.environ.get("SCHALE_HOST", "0.0.0.0"), help="Listen host (default: 0.0.0.0)")
    parser.add_argument("--uooc-port", type=int, default=int(os.environ.get("UOOC_PORT", "51088")), help="UOOC port (default: 51088)")
    parser.add_argument("--zhs-port", type=int, default=int(os.environ.get("ZHS_PORT", "51089")), help="Zhihuishu port (default: 51089)")
    parser.add_argument("--cx-port", type=int, default=int(os.environ.get("CX_PORT", "51092")), help="Chaoxing port (default: 51092)")
    parser.add_argument("--only", default="", help="Comma-separated platforms to run: uooc,zhs,cx (default: all)")
    parser.add_argument("--data-dir", default="", help="Custom persistent data directory (default: ./data)")
    args = parser.parse_args()

    if args.data_dir:
        os.environ["SCHALE_DATA_DIR"] = args.data_dir

    data_dir = get_data_dir()
    enabled = [x.strip().lower() for x in args.only.split(",") if x.strip()] if args.only else ["uooc", "zhs", "cx"]

    print("=" * 62)
    print("  S.C.H.A.L.E. Course Suite (Blue Archive Edition)")
    print(f"  Data Directory : {data_dir}")
    print(f"  Listen Host    : {args.host}")
    print("=" * 62)

    mgr = SuiteManager(
        host=args.host,
        uooc_port=args.uooc_port,
        zhs_port=args.zhs_port,
        cx_port=args.cx_port,
        enabled=enabled,
    )
    mgr.start_all()

    stop_flag = threading.Event()

    def _handle_sig(signum, frame):
        print("\n收到退出信号，正在优雅关闭所有服务...")
        stop_flag.set()

    signal.signal(signal.SIGINT, _handle_sig)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_sig)

    try:
        while not stop_flag.is_set():
            time.sleep(0.5)
    finally:
        mgr.stop_all()
        print("已全部退出。")


if __name__ == "__main__":
    main()
