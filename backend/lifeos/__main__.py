"""Run the backend: python -m lifeos (also the entry point of the packaged lifeos-backend.exe)."""
import uvicorn

from . import config


def main() -> None:
    # Import the app object directly instead of the "lifeos.main:app" string so PyInstaller sees the import.
    from .main import app

    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="info")


if __name__ == "__main__":
    main()
