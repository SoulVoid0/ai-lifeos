"""Run the backend: python -m lifeos"""
import uvicorn

from . import config


def main() -> None:
    uvicorn.run("lifeos.main:app", host=config.HOST, port=config.PORT, log_level="info")


if __name__ == "__main__":
    main()
