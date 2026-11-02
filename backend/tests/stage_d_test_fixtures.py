"""Compatibility re-export for Stage D test fixtures.

New tests import from ``test_support.stage_d_fixtures`` so production test
collection does not depend on ``backend/tests`` being a Python package.
"""

from test_support.stage_d_fixtures import *  # noqa: F401,F403
