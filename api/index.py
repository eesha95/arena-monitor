"""Vercel entry point. Reuses the local server's request handler in serverless mode:
no background threads; each /api/state request refreshes the sources that are due, and the page polls.
Runtime data lives in /tmp (per instance, not kept), so player-count history is local-app only."""
import os
import sys

os.environ.setdefault("ARENA_SERVERLESS", "1")
os.environ.setdefault("ARENA_DATA", "/tmp/arena")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import Handler  # noqa: E402


class handler(Handler):
    pass
