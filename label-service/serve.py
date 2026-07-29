#!/usr/bin/env python3
"""Production entrypoint used by systemd."""
from __future__ import annotations

from waitress import serve

import config
from run import app


if __name__ == "__main__":
    serve(app, host=config.HOST, port=config.PORT, threads=config.WAITRESS_THREADS)
