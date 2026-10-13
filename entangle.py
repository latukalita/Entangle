#!/usr/bin/env python3
"""Entangle: one-click remote Jupyter. Opens the SSH tunnel, starts/finds
Jupyter on the server, and shows it inside this window (no terminal, no browser)."""
import json, os, re, secrets, shlex, socket, sys, threading, socketserver
from pathlib import Path

import paramiko
from PySide6.QtCore import QEasingCurve, QObject, QPropertyAnimation, Qt, QThread, QUrl, Signal
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QApplication, QCheckBox, QFileDialog, QFrame, QGraphicsDropShadowEffect,
                               QGraphicsOpacityEffect, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
                               QProgressBar, QPushButton, QSpinBox, QStackedWidget, QVBoxLayout, QWidget)

CFG = Path.home() / ".guide_runner.json"
DEFAULTS = {"host": "", "user": "", "port": 0, "key": "", "threads": 4}

# BLAS/OpenMP libraries default to one thread per core; for small matrices (e.g. 100x100 expm) that is
# 40x+ slower than a few threads. Set before Jupyter starts so every kernel inherits it.
THREAD_VARS = ("OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS")


# Locate jupyter-lab on the server: PATH first, then common install spots (pip --user, conda, brew).
FIND_JUPYTER = ('J=$(command -v jupyter-lab || ls ~/Library/Python/*/bin/jupyter-lab ~/.local/bin/jupyter-lab '
                '/opt/anaconda3/bin/jupyter-lab ~/anaconda3/bin/jupyter-lab ~/miniconda3/bin/jupyter-lab '
                '/opt/homebrew/bin/jupyter-lab /usr/local/bin/jupyter-lab 2>/dev/null | head -1); [ -n "$J" ]')


class Forwarder(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, transport, remote_port):
        self.transport, self.remote_port = transport, remote_port
        super().__init__(("127.0.0.1", 0), _Handler)


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            ch = self.server.transport.open_channel(
                "direct-tcpip", ("127.0.0.1", self.server.remote_port), self.request.getpeername())
        except Exception:
            return
        def pump(a, b):
            try:
                while (d := a.recv(32768)):
                    b.sendall(d)
            except Exception:
                pass
            finally:
                for s in (a, b):
                    try: s.close()
                    except Exception: pass
        t = threading.Thread(target=pump, args=(ch, self.request), daemon=True); t.start()
        pump(self.request, ch)


class Connector(QThread):
    status = Signal(str)
    ready = Signal(str)
    failed = Signal(str)

    def __init__(self, host, user, password, key, port, start_if_missing, threads=4, restart=False):
        super().__init__()
        self.a = (host, user, password, key, port, start_if_missing)
        self.threads, self.restart = threads, restart
        self.thread_note = ""
        self.client = self.fwd = None

    def run_cmd(self, cmd):
        _, out, _ = self.client.exec_command(cmd)
        return out.read().decode()

    def run(self):
        host, user, password, key, port, start = self.a
        try:
            self.status.emit("Connecting over SSH…")
            self.client = paramiko.SSHClient()
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.client.connect(host, username=user, password=password or None,
                                key_filename=key or None, timeout=15,
                                allow_agent=True, look_for_keys=True)
            self.status.emit("Looking for Jupyter on the server…")
            token = None
            if port == 0:                          # Auto: reuse OUR OWN server, never someone else's
                st = self.read_state()
                if st and self.alive(st["port"], st["token"]):
                    if self.restart:               # opt-in: stops our server (and its kernels) so new settings apply
                        self.status.emit("Restarting Jupyter with the thread limit…")
                        self.sh(f"curl -s -X POST -H 'Authorization: token {st['token']}' "
                                f"'http://127.0.0.1:{st['port']}/api/shutdown' >/dev/null")
                        for _ in range(20):
                            if not self.alive(st["port"], st["token"]): break
                            self.msleep(500)
                    else:
                        port, token = st["port"], st["token"]
                        if st.get("threads") != self.threads:
                            self.thread_note = "thread limit not applied to this running server"
            else:                                  # manual port: use whatever is there, else start
                token = dict(self.list_servers()).get(port)
            if token is None:
                if not start:
                    raise RuntimeError("No Jupyter running and auto-start is off.")
                if port == 0:
                    port = int(self.sh("python3 -c \"import socket;s=socket.socket();s.bind(('127.0.0.1',0));"
                                       "print(s.getsockname()[1])\"").strip().splitlines()[-1])
                self.status.emit(f"Starting JupyterLab on server port {port}…")
                token = secrets.token_hex(16)
                env = " ".join(f"{v}={self.threads}" for v in THREAD_VARS) if self.threads else ""
                self.sh(FIND_JUPYTER + f' && {env} nohup "$J" --no-browser --ip=127.0.0.1 --port={port} '
                        f'--ServerApp.token={token} --ServerApp.port_retries=0 '
                        '> ~/.guide_runner_jupyter.log 2>&1 &')
                for _ in range(60):
                    if self.alive(port, token): break
                    self.msleep(500)
                else:
                    log = self.sh("tail -n 8 ~/.guide_runner_jupyter.log 2>&1").strip()
                    raise RuntimeError("Jupyter did not start. Server log: " + (log or "(empty - jupyter not found?)"))
                self.sh(f"echo '{json.dumps({'port': port, 'token': token, 'threads': self.threads})}' > ~/.guide_runner_state.json")
            self.server_port = port
            self.fwd = Forwarder(self.client.get_transport(), port)
            threading.Thread(target=self.fwd.serve_forever, daemon=True).start()
            lp = self.fwd.server_address[1]
            self.ready.emit(f"http://127.0.0.1:{lp}/lab?token={token}")
        except Exception as e:
            self.failed.emit(str(e))

    def sh(self, cmd):
        """Run on the server inside a login shell so PATH (zsh/bash profiles) is loaded."""
        return self.run_cmd("${SHELL:-/bin/sh} -lc " + shlex.quote(cmd))

    def alive(self, port, token):
        code = self.sh(f"curl -s -o /dev/null -w '%{{http_code}}' 'http://127.0.0.1:{port}/api/status?token={token}'")
        return code.strip().endswith("200")

    def read_state(self):
        try:
            return json.loads(self.sh("cat ~/.guide_runner_state.json 2>/dev/null"))
        except Exception:
            return None

    def list_servers(self):
        """[(port, token)] of Jupyter servers visible to `jupyter server list`."""
        out = self.sh(FIND_JUPYTER.replace('jupyter-lab', 'jupyter') + ' && "$J" server list 2>/dev/null')
        res = []
        for m in re.finditer(r"https?://[^\s:]+:(\d+)/\S*", out):
            t = re.search(r"token=(\w+)", m.group(0))
            res.append((int(m.group(1)), t.group(1) if t else ""))
        return res

    def close(self):
        try:
            if self.fwd: self.fwd.shutdown()
            if self.client: self.client.close()
        except Exception:
            pass


LOGO_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#1b1f3b"/><stop offset="0.55" stop-color="#4b3aa8"/><stop offset="1" stop-color="#b05cf0"/></linearGradient>
    <linearGradient id="r1" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#ffffff"/><stop offset="1" stop-color="#d9ccff"/></linearGradient>
    <linearGradient id="r2" x1="1" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#8fe3ff"/><stop offset="1" stop-color="#ffffff"/></linearGradient>
    <clipPath id="low"><rect x="0" y="128" width="256" height="128"/></clipPath>
  </defs>
  <rect width="256" height="256" rx="56" fill="url(#bg)"/>
  <circle cx="98" cy="128" r="50" fill="none" stroke="url(#r1)" stroke-width="15"/>
  <circle cx="158" cy="128" r="50" fill="none" stroke="url(#r2)" stroke-width="15"/>
  <circle cx="98" cy="128" r="50" fill="none" stroke="url(#r1)" stroke-width="15" clip-path="url(#low)"/>
</svg>
"""


def app_icon():
    from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
    from PySide6.QtSvg import QSvgRenderer
    r = QSvgRenderer(LOGO_SVG); img = QImage(256, 256, QImage.Format_ARGB32); img.fill(0)
    p = QPainter(img); r.render(p); p.end()
    return QIcon(QPixmap.fromImage(img))


QSS = """
* { font-family: "Inter", "Segoe UI", "Noto Sans", "Ubuntu", sans-serif; font-size: 14px; }
QMainWindow, #welcome { background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #1b1f3b, stop:0.55 #2d2a5e, stop:1 #7a3f8f); }
#card { background: rgba(255,255,255,0.97); border-radius: 22px; }
#title { font-size: 30px; font-weight: 700; color: #1d1f3a; }
#subtitle { color: #6b6f8a; font-size: 14px; }
#fieldlabel { color: #4a4e69; font-size: 12px; font-weight: 600; }
QLineEdit, QSpinBox { background: #f3f4fa; border: 1.5px solid #e2e4f0; border-radius: 11px;
                      padding: 10px 12px; color: #1d1f3a; selection-background-color: #7c6cf0; }
QLineEdit:focus, QSpinBox:focus { border: 1.5px solid #7c6cf0; background: #ffffff; }
QSpinBox::up-button, QSpinBox::down-button { width: 0; }
QCheckBox { color: #4a4e69; spacing: 8px; }
QCheckBox::indicator { width: 18px; height: 18px; border-radius: 6px; border: 1.5px solid #c9cce0; background: #fff; }
QCheckBox::indicator:checked { background: #7c6cf0; border: 1.5px solid #7c6cf0; }
#connect { color: white; font-size: 15px; font-weight: 600; border: none; border-radius: 12px; padding: 13px;
           background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #6a5af0, stop:1 #b05cf0); }
#connect:hover { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #7a6bff, stop:1 #c26dff); }
#connect:pressed { padding-top: 15px; }
#connect:disabled { background: #c9cce0; }
#link { color: #7c6cf0; background: transparent; border: none; font-weight: 600; text-align: left; padding: 2px 0; }
#link:hover { color: #5a49d6; }
#status { color: #6b6f8a; font-size: 13px; }
#status[err="true"] { color: #d6455d; }
QProgressBar { background: transparent; border: none; max-height: 4px; }
QProgressBar::chunk { border-radius: 2px; background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #6a5af0, stop:1 #b05cf0); }
#topbar { background: #14162b; }
#pill { color: #e8e9f7; font-size: 13px; }
#ghost { color: #d9daf0; background: rgba(255,255,255,0.08); border: none; border-radius: 9px; padding: 7px 16px; font-weight: 600; }
#ghost:hover { background: rgba(255,255,255,0.18); }
#danger:hover { background: #d6455d; color: white; }
"""


def labelled(text, widget):
    box = QVBoxLayout(); box.setSpacing(5)
    l = QLabel(text); l.setObjectName("fieldlabel")
    box.addWidget(l); box.addWidget(widget)
    return box


def fade_in(widget, ms=450):
    eff = QGraphicsOpacityEffect(widget); widget.setGraphicsEffect(eff)
    anim = QPropertyAnimation(eff, b"opacity", widget)
    anim.setDuration(ms); anim.setStartValue(0.0); anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.OutCubic)
    anim.finished.connect(lambda: widget.setGraphicsEffect(None))
    anim.start(QPropertyAnimation.DeleteWhenStopped)


class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Entangle")
        self.resize(1300, 850)
        cfg = {**DEFAULTS, **(json.loads(CFG.read_text()) if CFG.exists() else {})}
        self.conn = None

        self.host = QLineEdit(cfg["host"]); self.user = QLineEdit(cfg["user"])
        self.host.setPlaceholderText("Server address, e.g. 192.168.1.10"); self.user.setPlaceholderText("Your username on the server")
        self.pw = QLineEdit(); self.pw.setEchoMode(QLineEdit.Password)
        self.pw.setPlaceholderText("Leave blank to use your SSH key")
        self.key = QLineEdit(cfg["key"]); self.key.setPlaceholderText("Optional key file")
        self.port = QSpinBox(); self.port.setRange(0, 65535); self.port.setSpecialValueText("Auto")
        self.port.setValue(cfg["port"])
        self.auto = QCheckBox("Start Jupyter if it isn't running"); self.auto.setChecked(True)
        self.threads = QSpinBox(); self.threads.setRange(0, 64); self.threads.setSpecialValueText("No limit")
        self.threads.setValue(cfg["threads"])
        self.restart = QCheckBox("Restart Jupyter now to apply threads (stops running kernels)")
        for w in (self.host, self.user, self.pw):
            w.returnPressed.connect(self.toggle)

        # ---------- welcome page ----------
        card = QFrame(); card.setObjectName("card"); card.setFixedWidth(430)
        sh = QGraphicsDropShadowEffect(card); sh.setBlurRadius(60); sh.setOffset(0, 18); sh.setColor(QColor(0, 0, 0, 110))
        card.setGraphicsEffect(sh)
        title = QLabel("Entangle"); title.setObjectName("title")
        sub = QLabel("Link to your server. Run code anywhere."); sub.setObjectName("subtitle")

        self.adv = QWidget(); av = QVBoxLayout(self.adv); av.setContentsMargins(0, 0, 0, 0); av.setSpacing(12)
        av.addLayout(labelled("KEY FILE", self.key)); av.addLayout(labelled("PORT", self.port))
        av.addLayout(labelled("BLAS THREADS PER KERNEL (4 recommended)", self.threads))
        av.addWidget(self.auto); av.addWidget(self.restart)
        self.adv.setVisible(False)
        self.advbtn = QPushButton("Advanced options ▾"); self.advbtn.setObjectName("link")
        self.advbtn.setCursor(Qt.PointingHandCursor); self.advbtn.clicked.connect(self.toggle_adv)

        self.btn = QPushButton("Connect"); self.btn.setObjectName("connect")
        self.btn.setCursor(Qt.PointingHandCursor); self.btn.clicked.connect(self.toggle)
        self.bar = QProgressBar(); self.bar.setTextVisible(False); self.bar.setRange(0, 1); self.bar.setValue(0)
        self.msg = QLabel(""); self.msg.setObjectName("status"); self.msg.setWordWrap(True); self.msg.setAlignment(Qt.AlignCenter)

        cl = QVBoxLayout(card); cl.setContentsMargins(36, 34, 36, 30); cl.setSpacing(14)
        cl.addWidget(title); cl.addWidget(sub); cl.addSpacing(8)
        cl.addLayout(labelled("SERVER", self.host)); cl.addLayout(labelled("USER", self.user))
        cl.addLayout(labelled("PASSWORD", self.pw)); cl.addWidget(self.advbtn); cl.addWidget(self.adv)
        cl.addSpacing(4); cl.addWidget(self.btn); cl.addWidget(self.bar); cl.addWidget(self.msg)

        welcome = QWidget(); welcome.setObjectName("welcome")
        wl = QVBoxLayout(welcome); wl.addStretch(1); wl.addWidget(card, 0, Qt.AlignHCenter); wl.addStretch(1)
        self.welcome = welcome

        # ---------- session page ----------
        top = QFrame(); top.setObjectName("topbar"); top.setFixedHeight(46)
        tl = QHBoxLayout(top); tl.setContentsMargins(16, 0, 12, 0)
        self.pill = QLabel(""); self.pill.setObjectName("pill")
        name = QLabel("Entangle"); name.setObjectName("pill"); name.setStyleSheet("font-weight:700;")
        self.disc = QPushButton("Disconnect"); self.disc.setObjectName("ghost"); self.disc.setCursor(Qt.PointingHandCursor)
        self.disc.setStyleSheet("")  # inherits #ghost
        self.disc.clicked.connect(self.disconnect)
        tl.addWidget(name); tl.addSpacing(18); tl.addWidget(self.pill); tl.addStretch(1); tl.addWidget(self.disc)
        self.load = QProgressBar(); self.load.setTextVisible(False); self.load.setFixedHeight(3)
        self.view = QWebEngineView()
        self.view.loadProgress.connect(self.load.setValue)
        self.view.loadFinished.connect(lambda _: self.load.setVisible(False))
        self.view.page().profile().downloadRequested.connect(self.on_download)
        self.session = QWidget(); sl = QVBoxLayout(self.session); sl.setContentsMargins(0, 0, 0, 0); sl.setSpacing(0)
        sl.addWidget(top); sl.addWidget(self.load); sl.addWidget(self.view, 1)

        self.stack = QStackedWidget(); self.stack.addWidget(welcome); self.stack.addWidget(self.session)
        self.setCentralWidget(self.stack)
        fade_in(card, 700)

    def toggle_adv(self):
        show = not self.adv.isVisible()
        self.adv.setVisible(show)
        self.advbtn.setText("Advanced options ▴" if show else "Advanced options ▾")

    def set_msg(self, text, err=False):
        self.msg.setText(text); self.msg.setProperty("err", err)
        self.msg.style().unpolish(self.msg); self.msg.style().polish(self.msg)

    def toggle(self):
        if not self.btn.isEnabled():
            return
        CFG.write_text(json.dumps({"host": self.host.text(), "user": self.user.text(),
                                   "port": self.port.value(), "key": self.key.text(),
                                   "threads": self.threads.value()}))
        self.btn.setEnabled(False); self.btn.setText("Connecting…")
        self.bar.setRange(0, 0)                       # smooth indeterminate animation
        self.set_msg("Starting…")
        self.conn = Connector(self.host.text(), self.user.text(), self.pw.text(),
                              self.key.text(), self.port.value(), self.auto.isChecked(),
                              self.threads.value(), self.restart.isChecked())
        self.restart.setChecked(False)
        self.conn.status.connect(self.set_msg)
        self.conn.ready.connect(self.on_ready)
        self.conn.failed.connect(self.on_fail)
        self.conn.start()

    def on_ready(self, url):
        self.bar.setRange(0, 1); self.bar.setValue(0)
        self.pill.setText(f"<span style='color:#4ade80'>●</span>&nbsp; Connected to {self.host.text()}"
                          f" &nbsp;·&nbsp; server port {getattr(self.conn, 'server_port', '?')}"
                          + (f" &nbsp;·&nbsp; <span style='color:#fbbf24'>⚠ {self.conn.thread_note}"
                             " (tick Restart in Advanced)</span>" if self.conn.thread_note else
                             (f" &nbsp;·&nbsp; {self.threads.value()} BLAS threads" if self.threads.value() else "")))
        self.load.setRange(0, 100); self.load.setValue(0); self.load.setVisible(True)
        self.view.load(QUrl(url))
        self.stack.setCurrentWidget(self.session); fade_in(self.session, 500)

    def on_download(self, dl):
        """JupyterLab's Download / Save-as: Qt cancels downloads unless the app accepts them."""
        start = str(Path.home() / "Downloads" / Path(dl.downloadFileName()).name)
        path, _ = QFileDialog.getSaveFileName(self, "Save file", start)
        if not path:
            dl.cancel(); return
        dl.setDownloadDirectory(str(Path(path).parent)); dl.setDownloadFileName(Path(path).name)
        dl.accept()

    def on_fail(self, err):
        self.bar.setRange(0, 1); self.bar.setValue(0)
        self.btn.setEnabled(True); self.btn.setText("Connect")
        self.set_msg(f"Couldn't connect — {err}", err=True)

    def disconnect(self):
        if self.conn: self.conn.close()
        self.view.setUrl(QUrl("about:blank"))
        self.btn.setEnabled(True); self.btn.setText("Connect"); self.set_msg("Disconnected")
        self.stack.setCurrentWidget(self.welcome); fade_in(self.welcome, 400)

    def closeEvent(self, e):
        if self.conn: self.conn.close()
        super().closeEvent(e)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyleSheet(QSS); app.setWindowIcon(app_icon())
    w = Main(); w.show()
    sys.exit(app.exec())
