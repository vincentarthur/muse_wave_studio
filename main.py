#!/usr/bin/env python3
"""Main entrypoint for MuseWave Studio · Music Creation & Acoustic Intelligence (v1.1)."""
from __future__ import annotations

import argparse
import os
import uvicorn

from music_studio_app import app

__all__ = ["app", "main"]


def main() -> None:
  parser = argparse.ArgumentParser(
      description="MuseWave Studio · Music Creation & Acoustic Intelligence v1.1"
  )
  parser.add_argument(
      "--host",
      default=os.environ.get("HOST", "0.0.0.0"),
      help="Host interface to bind (default: 0.0.0.0)",
  )
  parser.add_argument(
      "--port",
      type=int,
      default=int(os.environ.get("PORT", "8080")),
      help="Port to listen on (default: $PORT or 8080)",
  )
  args = parser.parse_args()
  uvicorn.run(
      app,
      host=args.host,
      port=args.port,
      log_level="info",
      access_log=True,
  )


if __name__ == "__main__":
  main()
