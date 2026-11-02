"""Archived Agent-RAG V1 Standalone entrypoint.

Round 8 retired the duplicate Orchestrator/Checkpoint/TaskStatus control plane.
Use ``scripts/run_demo.py`` for the production Mainline, or run the mapped
Mainline E2E tests documented in
``docs/AGENT_RAG_STANDALONE_RETIREMENT_MATRIX_20260801.md``.
"""

from __future__ import annotations

import argparse
import sys


RETIRED_MESSAGE = (
    "run_agent_rag_v1_standalone.py was retired in Round 8. "
    "Use scripts/run_demo.py for the production Mainline."
)


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        description=RETIRED_MESSAGE,
    )


def main() -> int:
    build_parser().parse_args()
    print(RETIRED_MESSAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
