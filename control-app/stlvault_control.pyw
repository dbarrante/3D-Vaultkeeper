"""STLVault Control — a small desktop window for starting, stopping, and
checking on the backend + frontend dev servers, instead of juggling two
PowerShell terminals by hand.

Run by double-clicking this file (Windows opens .pyw with pythonw.exe,
so no console window appears), or `python stlvault_control.pyw` to see
tracebacks while developing it.
"""

import queue
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import scrolledtext, ttk

import process_manager as pm

POLL_SECONDS = 2
DRAIN_MS = 200


class ControlApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("STLVault Control")
        root.geometry("640x420")
        root.minsize(560, 360)

        self._events = queue.Queue()
        self._stop_polling = threading.Event()

        self._build_widgets()
        self._log(f"Backend target: http://localhost:{pm.BACKEND_PORT}")
        self._log(f"Frontend target: http://localhost:{pm.FRONTEND_PORT}")

        threading.Thread(target=self._poll_loop, daemon=True).start()
        root.after(DRAIN_MS, self._drain_events)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---- layout ----------------------------------------------------

    def _build_widgets(self):
        pad = {"padx": 8, "pady": 6}

        rows = ttk.Frame(self.root)
        rows.pack(fill="x", **pad)

        self.backend_row = self._build_service_row(
            rows, 0, "Backend", pm.BACKEND_PORT, self._start_backend, self._stop_backend
        )
        self.frontend_row = self._build_service_row(
            rows, 1, "Frontend", pm.FRONTEND_PORT, self._start_frontend, self._stop_frontend
        )
        rows.columnconfigure(1, weight=1)

        actions = ttk.Frame(self.root)
        actions.pack(fill="x", **pad)
        ttk.Button(actions, text="Start All", command=self._start_all).pack(side="left", padx=4)
        ttk.Button(actions, text="Stop All", command=self._stop_all).pack(side="left", padx=4)
        ttk.Button(actions, text="Restart All", command=self._restart_all).pack(side="left", padx=4)
        ttk.Button(actions, text="Open in Browser", command=self._open_browser).pack(side="left", padx=4)

        log_frame = ttk.Frame(self.root)
        log_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        ttk.Label(log_frame, text="Log:").pack(anchor="w")
        self.log_widget = scrolledtext.ScrolledText(log_frame, height=10, state="disabled", wrap="word")
        self.log_widget.pack(fill="both", expand=True)

    def _build_service_row(self, parent, row, name, port, on_start, on_stop):
        ttk.Label(parent, text=name, width=10).grid(row=row, column=0, sticky="w")
        status_var = tk.StringVar(value="checking...")
        ttk.Label(parent, textvariable=status_var, width=32).grid(row=row, column=1, sticky="w")
        ttk.Label(parent, text=f":{port}", width=8).grid(row=row, column=2, sticky="w")
        ttk.Button(parent, text="Start", command=on_start).grid(row=row, column=3, padx=2)
        ttk.Button(parent, text="Stop", command=on_stop).grid(row=row, column=4, padx=2)
        return status_var

    # ---- background actions (never block the UI thread) ------------

    def _run_async(self, fn):
        threading.Thread(target=self._safe_call, args=(fn,), daemon=True).start()

    def _safe_call(self, fn):
        try:
            fn(log_fn=self._queue_log)
        except (pm.SetupIncomplete, pm.PortInUse) as exc:
            self._queue_log(str(exc))

    def _start_backend(self):
        self._run_async(pm.start_backend)

    def _start_frontend(self):
        self._run_async(pm.start_frontend)

    def _stop_backend(self):
        self._run_async(lambda log_fn: pm.stop_process(
            pm.BACKEND_PIDFILE, pm.BACKEND_PORT, log_fn=log_fn, name="Backend"))

    def _stop_frontend(self):
        self._run_async(lambda log_fn: pm.stop_process(
            pm.FRONTEND_PIDFILE, pm.FRONTEND_PORT, log_fn=log_fn, name="Frontend"))

    def _start_all(self):
        self._start_backend()
        self._start_frontend()

    def _stop_all(self):
        self._stop_backend()
        self._stop_frontend()

    def _restart_all(self):
        def restart(log_fn):
            pm.stop_process(pm.BACKEND_PIDFILE, pm.BACKEND_PORT, log_fn=log_fn, name="Backend")
            pm.stop_process(pm.FRONTEND_PIDFILE, pm.FRONTEND_PORT, log_fn=log_fn, name="Frontend")
            time.sleep(1)
            pm.start_backend(log_fn=log_fn)
            pm.start_frontend(log_fn=log_fn)
        self._run_async(restart)

    def _open_browser(self):
        webbrowser.open(f"http://localhost:{pm.FRONTEND_PORT}/")

    # ---- polling + thread-safe UI updates ---------------------------

    def _poll_loop(self):
        while not self._stop_polling.is_set():
            self._events.put(("status", pm.status()))
            self._stop_polling.wait(POLL_SECONDS)

    def _queue_log(self, message: str):
        self._events.put(("log", message))

    def _drain_events(self):
        try:
            while True:
                kind, payload = self._events.get_nowait()
                if kind == "status":
                    self._apply_status(payload)
                else:
                    self._log(payload)
        except queue.Empty:
            pass
        self.root.after(DRAIN_MS, self._drain_events)

    def _apply_status(self, result: dict):
        self.backend_row.set(self._describe(result["backend"]))
        self.frontend_row.set(self._describe(result["frontend"]))

    @staticmethod
    def _describe(entry: dict) -> str:
        if not entry["process_running"]:
            return "stopped"
        state = "healthy" if entry["healthy"] else "not responding"
        return f"running ({state})" if entry["tracked"] else f"running, untracked ({state})"

    def _log(self, message: str):
        self.log_widget.configure(state="normal")
        self.log_widget.insert("end", f"{time.strftime('%H:%M:%S')}  {message}\n")
        self.log_widget.see("end")
        self.log_widget.configure(state="disabled")

    def _on_close(self):
        self._stop_polling.set()
        self.root.destroy()


def main():
    root = tk.Tk()
    ControlApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
