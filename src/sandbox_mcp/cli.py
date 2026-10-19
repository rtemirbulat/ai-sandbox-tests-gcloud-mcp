import argparse
import logging

import uvicorn

from sandbox_mcp.settings import Settings


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    p = argparse.ArgumentParser("sandbox-mcp")
    p.add_argument("command", choices=["serve", "worker"])
    args = p.parse_args()
    settings = Settings()
    if args.command == "serve":
        from sandbox_mcp.mcp.app import build_app

        uvicorn.run(build_app(settings), host=settings.host, port=settings.port, log_level="info")
    else:
        from sandbox_mcp.worker import run_worker

        run_worker(settings)


if __name__ == "__main__":
    main()
