"""Launch StatLab as a local web app and open it in the default browser."""
from __future__ import annotations

import threading
import webbrowser

import uvicorn

from app.core.config import HOST, PORT


def main() -> None:
    threading.Timer(1.2, lambda: webbrowser.open(f"http://{HOST}:{PORT}")).start()
    uvicorn.run("app.main:app", host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
