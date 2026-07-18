"""Backward-compatible alias for the real project-input normalizer."""

from .project_input_normalizer_agent import ProjectInputNormalizerAgent


class FakeTableAgent(ProjectInputNormalizerAgent):
    """Deprecated compatibility name. No fake business fallback remains."""

    agent_name = "FakeTableAgent"
