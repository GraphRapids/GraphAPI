import os

import uvicorn


def main() -> int:
    host = os.getenv("GRAPHAPI_HOST", "0.0.0.0")
    port = int(os.getenv("GRAPHAPI_PORT", os.getenv("PORT", "8000")))
    uvicorn.run("graphapi.app:app", host=host, port=port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
