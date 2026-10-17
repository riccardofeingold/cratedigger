"""Start Crate Digger: python -m cratedigger [--port 8765] [--no-browser]"""

from __future__ import annotations

import argparse
import logging
import secrets
import signal
import socket
import sys
import threading
import webbrowser

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from werkzeug.serving import make_server

from . import APP_NAME, __version__
from .config import SettingsStore, config_dir
from .library import Library
from .server import create_app
from .services import AnalysisQueue, JobManager
from .sources import build_sources

log = logging.getLogger("cratedigger")


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers["User-Agent"] = f"CrateDigger/{__version__} (personal DJ library app)"
    retry = Retry(total=2, backoff_factor=0.5, status_forcelist=(502, 503, 504), allowed_methods=("GET",))
    adapter = HTTPAdapter(pool_connections=8, pool_maxsize=16, max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _port_in_use(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def _already_running(port: int) -> bool:
    try:
        r = requests.get(f"http://127.0.0.1:{port}/health", timeout=1,
                         proxies={"http": None, "https": None})
        return r.ok and r.json().get("app") == "cratedigger"
    except (requests.RequestException, ValueError):
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cratedigger", description=f"{APP_NAME}: dig for music, build a DJ crate.")
    parser.add_argument("--port", type=int, default=8765, help="port to listen on (default 8765)")
    parser.add_argument("--no-browser", action="store_true", help="don't open the browser automatically")
    parser.add_argument("--verbose", action="store_true", help="log more detail")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(message)s")
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.DEBUG if args.verbose else logging.ERROR)

    port = args.port
    for candidate in range(args.port, args.port + 10):
        if not _port_in_use(candidate):
            port = candidate
            break
        if _already_running(candidate):
            url = f"http://127.0.0.1:{candidate}/"
            print(f"{APP_NAME} is already running at {url}")
            if not args.no_browser:
                webbrowser.open(url)
            return 0
    else:
        port = 0  # let the OS pick a free port

    home = config_dir()
    home.mkdir(parents=True, exist_ok=True)
    settings_store = SettingsStore(home / "settings.json")
    settings_store.get().library_path.mkdir(parents=True, exist_ok=True)
    library = Library(home / "library.sqlite3")
    session = make_session()
    sources = build_sources(settings_store, session)
    analysis = AnalysisQueue(library)
    jobs = JobManager(library, settings_store, sources, analysis, session)
    app = create_app(settings_store=settings_store, library=library, sources=sources, jobs=jobs,
                     analysis=analysis, token=secrets.token_urlsafe(24))

    server = make_server("127.0.0.1", port, app, threaded=True)
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"{APP_NAME} {__version__} is running at {url}")
    print(f"Library folder: {settings_store.get().library_path}")
    print("Press Ctrl+C to quit.")
    analysis.resume()
    if not args.no_browser:
        threading.Timer(0.8, webbrowser.open, [url]).start()

    def stop(signum, frame):  # treat "terminal closed" / kill like Ctrl+C
        raise KeyboardInterrupt

    for sig in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGHUP", None)):
        if sig is not None:
            signal.signal(sig, stop)
    try:
        server.serve_forever()  # Werkzeug returns from here on Ctrl+C
    except KeyboardInterrupt:
        pass
    finally:
        print("\nStopping…")
        jobs.shutdown()
        analysis.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
