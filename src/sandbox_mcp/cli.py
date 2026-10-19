import argparse
import logging

import uvicorn

from sandbox_mcp.settings import Settings


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    p = argparse.ArgumentParser("sandbox-mcp")
    p.add_argument("command", choices=["serve", "worker", "token"])
    p.add_argument("user", nargs="?", help="for `token`: user id (e.g. corporate login)")
    args = p.parse_args()
    settings = Settings()
    if args.command == "serve":
        from sandbox_mcp.mcp.app import build_app

        uvicorn.run(build_app(settings), host=settings.host, port=settings.port, log_level="info")
    elif args.command == "worker":
        from sandbox_mcp.worker import run_worker

        run_worker(settings)
    else:
        if not args.user:
            p.error("token requires <user>")
        from sandbox_mcp.db.repo import Repository
        from sandbox_mcp.db.schema import ensure_schema
        from sandbox_mcp.db.session import make_engine, make_session_factory

        engine = make_engine(settings.database_url)
        ensure_schema(engine)
        print(Repository(make_session_factory(engine)).create_user_token(args.user))


if __name__ == "__main__":
    main()
