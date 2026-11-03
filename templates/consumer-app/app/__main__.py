"""uvicorn entry point: ``python -m app`` (or ``bin/web``)."""

from __future__ import annotations

import uvicorn
from pw_foundation.utils.env import resolve_bool

from app.config import cfg  # loads .env

if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host=cfg.WEB_HOST,
        port=cfg.WEB_PORT,
        reload=resolve_bool(None, "WEB_RELOAD", default=False),
    )
