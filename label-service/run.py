#!/usr/bin/env python3
"""Development entrypoint. Production deployment uses waitress via systemd
(see label-service.service) rather than the Flask development server."""
from __future__ import annotations

import config
from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host=config.HOST, port=config.PORT, debug=False)
