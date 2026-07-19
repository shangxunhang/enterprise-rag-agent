"""Architecture regression tests for the retrieval/generation boundary."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_rag_runtime_is_retrieval_only() -> None:
    removed_pipeline = (
        PROJECT_ROOT / "backend/rag/application/parent_child_generation.py"
    )
    engine_source = (
        PROJECT_ROOT / "backend/rag/rag_engine/parent_child_rag_engine.py"
    ).read_text(encoding="utf-8")

    assert not removed_pipeline.exists()
    assert "ParentChildGenerationPipeline" not in engine_source
    assert "PromptBuilder" not in engine_source
    assert "generation_checker" not in engine_source
    assert "repair_strategy" not in engine_source
    assert "generate_answer" not in engine_source
    assert "self.context_gate.pack(" in engine_source
