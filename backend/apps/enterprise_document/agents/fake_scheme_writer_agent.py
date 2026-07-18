"""Backward-compatible alias for the section-oriented writer."""

from .scheme_writer_agent import SchemeWriterAgent


class FakeSchemeWriterAgent(SchemeWriterAgent):
    """Deprecated compatibility name. The implementation is no longer fake."""

    agent_name = "FakeSchemeWriterAgent"
