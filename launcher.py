"""
Stocks Web App Launcher
Double-click to start all services and open the browser.
"""
import os
import sys
import subprocess
import threading
import webbrowser
import time
import socket
import tkinter as tk
from tkinter import ttk

# ── パス設定 ──────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
BACKEND_DIR  = os.path.join(BASE_DIR, "backend")
PY_VENV      = os.path.join(BASE_DIR, ".venv", "Scripts", "python.exe")
PY_SYS       = sys.executable  # fallback

PORTS = [3000, 8000, 8010]
APP_URL = "http://127.0.0.1:3000/screener.html"

# ── ポート解放 ────────────────────────────────────────────
def kill_ports():
    for port in PORTS:
        try:
            out = subprocess.check_output(
                f'netstat -ano | findstr :{port} | findstr LISTENING',
                shell=True, stderr=subprocess.DEVNULL
            ).decode()
            for line in out.splitlines():
                parts = line.split()
                if parts:
                    pid = parts[-1]
                    subprocess.call(f"taskkill /PID {pid} /F",
                                    shell=True, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
        except Exception:
            pass

# ── ポート待機 ────────────────────────────────────────────
def wait_for_port(port, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False

# ── ランチャー本体 ─────────────────────────────────────────
class AppLauncher:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.processes: list[subprocess.Popen] = []
        self._build_ui()
        threading.Thread(target=self._start_services, daemon=True).start()

    # ── UI ────────────────────────────────────────────────
    def _build_ui(self):
        self.root.title("Stocks Web App")
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)

        pad = dict(padx=14, pady=6)

        self.title_lbl = tk.Label(self.root, text="Stocks Web App",
                                  font=("Segoe UI", 13, "bold"))
        self.title_lbl.pack(padx=14, pady=(14, 2))

        self.status_lbl = tk.Label(self.root, text="起動中...",
                                   font=("Segoe UI", 10), fg="#666")
        self.status_lbl.pack(**pad)

        self.progress = ttk.Progressbar(self.root, mode="indeterminate",
                                        length=260)
        self.progress.pack(padx=14, pady=4)
        self.progress.start(12)

        frame = tk.Frame(self.root)
        frame.pack(padx=14, pady=(8, 14))

        self.open_btn = tk.Button(frame, text="ブラウザで開く",
                                  font=("Segoe UI", 10),
                                  command=self._open_browser,
                                  state="disabled", width=14)
        self.open_btn.grid(row=0, column=0, padx=4)

        self.stop_btn = tk.Button(frame, text="停止して終了",
                                  font=("Segoe UI", 10), fg="#c00",
                                  command=self._stop_all, width=14)
        self.stop_btn.grid(row=0, column=1, padx=4)

        self.root.protocol("WM_DELETE_WINDOW", self._stop_all)

    def _set_status(self, msg, color="#333"):
        self.root.after(0, lambda: self.status_lbl.config(text=msg, fg=color))

    def _set_ready(self):
        def _do():
            self.progress.stop()
            self.progress.pack_forget()
            self.status_lbl.config(text="実行中  ✓", fg="#080", font=("Segoe UI", 10, "bold"))
            self.open_btn.config(state="normal")
        self.root.after(0, _do)

    # ── サービス起動 ──────────────────────────────────────
    def _start_services(self):
        self._set_status("古いプロセスを停止中...")
        kill_ports()
        time.sleep(0.8)

        py_venv = PY_VENV if os.path.exists(PY_VENV) else PY_SYS

        # Frontend (port 3000)
        self._set_status("フロントエンド起動中 (3000)...")
        fe_proc = subprocess.Popen(
            [py_venv, "-m", "http.server", "3000"],
            cwd=FRONTEND_DIR,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        self.processes.append(fe_proc)

        # Backend (port 8010)
        self._set_status("バックエンド起動中 (8010)...")
        be_proc = subprocess.Popen(
            [py_venv, "-m", "uvicorn", "app.main:app",
             "--host", "0.0.0.0", "--port", "8010"],
            cwd=BACKEND_DIR,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        self.processes.append(be_proc)

        # Backend (port 8000) — サブ
        be_proc2 = subprocess.Popen(
            [py_venv, "-m", "uvicorn", "app.main:app",
             "--host", "0.0.0.0", "--port", "8000"],
            cwd=BACKEND_DIR,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        self.processes.append(be_proc2)

        # ポート待機
        self._set_status("ポート 3000 を待機中...")
        ok3000 = wait_for_port(3000, timeout=40)
        self._set_status("ポート 8010 を待機中...")
        ok8010 = wait_for_port(8010, timeout=40)

        if ok3000 and ok8010:
            self._set_ready()
            self._open_browser()
        else:
            self._set_status("起動に失敗しました", color="#c00")

    def _open_browser(self):
        webbrowser.open(APP_URL)

    # ── 停止 ──────────────────────────────────────────────
    def _stop_all(self):
        for proc in self.processes:
            try:
                proc.terminate()
            except Exception:
                pass
        kill_ports()
        self.root.destroy()


# ── エントリーポイント ─────────────────────────────────────
def main():
    root = tk.Tk()
    root.geometry("300x160")
    AppLauncher(root)
    root.mainloop()


if __name__ == "__main__":
    main()
