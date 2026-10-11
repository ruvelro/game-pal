"""Prueba de humo de la app empaquetada: arranca, responde en loopback y se cierra.

python scripts/smoke.py dist/game-pal/game-pal            (Linux)
python scripts/smoke.py dist/game-pal/game-pal.exe        (Windows)
python scripts/smoke.py dist/game-pal.app/Contents/MacOS/game-pal
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

TIMEOUT_S = 120


def main() -> int:
    binary = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix="game-pal-smoke-") as home:
        env = {**os.environ, "GAMEPAL_HOME": home}
        process = subprocess.Popen([str(binary), "run", "--no-open", "--no-tray"], env=env)
        instance = Path(home) / "data" / "instance.json"
        try:
            deadline = time.monotonic() + TIMEOUT_S
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    print(f"La app se cerró al arrancar (código {process.returncode})")
                    return 1
                if instance.is_file():
                    port = json.loads(instance.read_text(encoding="utf-8"))["port"]
                    try:
                        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/instance", timeout=5) as response:
                            info = json.load(response)
                    except OSError:
                        time.sleep(1)
                        continue
                    print(f"OK: {info['app']} {info['version']} responde en el puerto {port}")
                    return 0
                time.sleep(1)
            print("La app no respondió a tiempo")
            return 1
        finally:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    sys.exit(main())
