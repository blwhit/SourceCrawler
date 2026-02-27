"""SourceCrawler - Public source code search engine."""
import logging
import sys

import uvicorn
import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)


def main():
    try:
        with open("config.yaml", "r") as f:
            config = yaml.safe_load(f) or {}
    except FileNotFoundError:
        config = {}

    server_config = config.get("server", {})
    host = server_config.get("host", "127.0.0.1")
    port = server_config.get("port", 8080)

    print(f"\n  SourceCrawler starting on http://{host}:{port}\n")

    uvicorn.run(
        "web.server:app",
        host=host,
        port=port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
