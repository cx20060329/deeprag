"""BCM-RAG API — Server entry point.

Usage:
    python -m api.main
    uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
"""

import uvicorn


def main():
    """Run the BCM-RAG API server."""
    uvicorn.run(
        "api:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )


if __name__ == "__main__":
    main()
