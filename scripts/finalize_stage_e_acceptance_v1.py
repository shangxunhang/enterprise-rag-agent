"""Deprecated compatibility entrypoint for the JUnit-backed Stage E V2 finalizer.

Stage E.1 removed all manually supplied test-count arguments. This module keeps
the historical filename executable while delegating only to the explicit V2 CLI.
"""

from __future__ import annotations

from finalize_stage_e_acceptance_v2 import main


if __name__ == "__main__":
    raise SystemExit(main())
