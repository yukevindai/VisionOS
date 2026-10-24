"""Run one photo through the pipeline and print the result JSON.

    python -m r3_vision.cli photo.jpg "Find floor 5"                     # live Gemini (needs GEMINI_API_KEY)
    python -m r3_vision.cli photo.jpg "Find floor 5" --replay reply.json  # SIMULATED, no network
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import gemini, pipeline

BACKEND = Path(__file__).resolve().parent.parent


def client_from_args(replay: Path | None) -> gemini.GeminiClient:
    gemini.load_dotenv(BACKEND / ".env")
    if replay is not None:
        envelope = json.loads(replay.read_text(encoding="utf-8"))
        return gemini.GeminiClient(api_key="replay", model="replay", transport=gemini.replay_transport(envelope), live=False)
    return gemini.GeminiClient()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("image", type=Path)
    parser.add_argument("request")
    parser.add_argument("--replay", type=Path, help="Gemini REST reply to replay instead of calling the API (SIMULATED)")
    args = parser.parse_args(argv)
    client = client_from_args(args.replay)
    if client.live and not client.configured:
        print("GEMINI_API_KEY and GEMINI_MODEL must both be set (environment or backend/.env). No live call was made.",
              file=sys.stderr)
    result = pipeline.run(args.image.read_bytes(), args.request, client)
    print(json.dumps(result, indent=2))
    return 0 if result["provider"]["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
