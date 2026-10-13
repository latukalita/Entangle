# Entangle

One-click remote Jupyter. Entangle connects to your server over SSH, starts (or finds) JupyterLab there, and shows it right inside its own window. No terminal, no port-forwarding commands, no browser.

## Install (Linux)

```bash
curl -fsSL https://raw.githubusercontent.com/latukalita/Entangle/main/install.sh | bash
```

This downloads the ready-to-run app (about 110 MB; nothing to build, no Python needed) and adds **Entangle** to your app menu, plus an `entangle` command.

Prefer to do it by hand? Download `Entangle-linux.tar.xz` from the [latest release](https://github.com/latukalita/Entangle/releases/latest), then:

```bash
tar -xJf Entangle-linux.tar.xz
./Entangle/Entangle
```

If the window doesn't open (common on X11 desktops), install one missing system library: `sudo apt install libxcb-cursor0`.

**Requirements**
- Your computer: 64-bit Linux.
- The server: SSH access, `python3`, `curl` and JupyterLab (`pip install jupyterlab`).

## Use

1. Enter the server address and your username.
2. Enter your password, or leave it blank to log in with your SSH key. If your key isn't the default one, pick it under **Advanced options**.
3. Click **Connect**. JupyterLab opens in the window; if it isn't already running on the server, Entangle starts it for you.

Entangle remembers your server, username and key-file location (never your password) in `~/.guide_runner.json`.

## Uninstall

```bash
curl -fsSL https://raw.githubusercontent.com/latukalita/Entangle/main/install.sh | bash -s -- --uninstall
```

## Run from source

```bash
pip install PySide6 paramiko
python3 entangle.py
```

To build the standalone app yourself: `pip install pyinstaller && pyinstaller Entangle.spec` (the app appears in `dist/Entangle/`).

## License

[MIT](LICENSE)
