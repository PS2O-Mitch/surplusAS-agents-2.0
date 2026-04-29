"""uvicorn entrypoint for the gateway."""

from __future__ import annotations

import uvicorn

from shared.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "service.app:app",
        host="0.0.0.0",  # noqa: S104  Cloud Run / local dev both need 0.0.0.0
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
