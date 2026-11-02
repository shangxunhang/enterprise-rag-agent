from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Iterable

import pytest

from contracts.rag import RAGServicePort
from core.runtime.execution_control import WorkflowExecutionCancelled
from model_gateway.call_boundary import ModelCallBudgetExceeded
from rag.integrations.langchain.runtime_facade import LangChainRAGServiceFacade
from rag.services.rag_service import FakeRAGService, RAGService
from schemas.rag import RAGToolInputSchema
from schemas.status import ExecutionStatus


BACKEND_ROOT = Path(__file__).resolve().parents[1]
UPPER_ROOTS = (
    BACKEND_ROOT / "application",
    BACKEND_ROOT / "apps",
    BACKEND_ROOT / "agent",
    BACKEND_ROOT / "context_manager",
)
RAG_ROOT = BACKEND_ROOT / "rag"
UPPER_PACKAGE_NAMES = {"application", "apps", "agent", "context_manager"}
FACADE_FORBIDDEN_TRAVERSAL_NAMES = {
    "delegate",
    "_delegate",
    "inner",
    "retrieval_runtime",
    "retriever",
    "reranker",
    "judge",
    "mapper",
    "config",
    "runnable",
}
FACADE_ACCEPTED_INSTANCE_STATE = {
    "_close",
    "_invocation_lock",
    "_runnable",
    "runnable_invocation_count",
}
FACADE_ACCEPTED_PUBLIC_SURFACE = {
    "abatch",
    "ainvoke",
    "as_runnable",
    "astream",
    "batch",
    "capabilities",
    "close",
    "invoke",
    "retrieve",
    "stream",
    "stream_semantics",
    "supports_native_async",
    "supports_token_streaming",
}
CONCRETE_RAG_IMPLEMENTATION_NAMES = {"RAGService", "FakeRAGService"}


def _is_test_path(path: Path) -> bool:
    """Return whether a Python file is test-only and excluded from production scans."""

    return (
        "tests" in path.parts
        or path.name == "conftest.py"
        or path.name.startswith("test_")
        or path.name.endswith("_test.py")
    )


def _python_files(roots: Iterable[Path]) -> list[Path]:
    paths: list[Path] = []
    for root in roots:
        paths.extend(
            path
            for path in sorted(root.rglob("*.py"))
            if not _is_test_path(path)
        )
    return paths


def _relative(path: Path) -> str:
    return path.relative_to(BACKEND_ROOT).as_posix()


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(BACKEND_ROOT).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _resolve_import_from(path: Path, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return str(node.module or "")

    module_name = _module_name(path)
    package_parts = module_name.split(".")
    if path.name != "__init__.py":
        package_parts = package_parts[:-1]
    keep = max(0, len(package_parts) - (node.level - 1))
    base = package_parts[:keep]
    if node.module:
        base.extend(node.module.split("."))
    return ".".join(base)


def _static_import_targets(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    targets: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            target = _resolve_import_from(path, node)
            if target:
                targets.append(target)
    return targets


def _dynamic_literal_import_targets(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    importlib_aliases = {"importlib"}
    import_module_aliases: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "importlib":
                    importlib_aliases.add(alias.asname or "importlib")
        elif isinstance(node, ast.ImportFrom) and node.module == "importlib":
            for alias in node.names:
                if alias.name == "import_module":
                    import_module_aliases.add(alias.asname or alias.name)

    targets: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        first_arg = node.args[0]
        if not isinstance(first_arg, ast.Constant) or not isinstance(first_arg.value, str):
            continue

        is_dynamic_import = False
        if isinstance(node.func, ast.Name):
            is_dynamic_import = (
                node.func.id == "__import__"
                or node.func.id in import_module_aliases
            )
        elif isinstance(node.func, ast.Attribute):
            is_dynamic_import = (
                node.func.attr == "import_module"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in importlib_aliases
            )

        if is_dynamic_import:
            targets.append(first_arg.value)
    return targets


def _is_package(target: str, package: str) -> bool:
    return target == package or target.startswith(f"{package}.")


def test_upper_production_has_only_frozen_step6_rag_dependencies() -> None:
    allowed = {
        (
            "apps/enterprise_document/agents/scheme_writer_agent.py",
            "rag.store.evidence_bundle_store",
        ),
        (
            "apps/enterprise_document/services/scheme_writer/evidence_service.py",
            "rag.store.evidence_bundle_store",
        ),
        (
            "apps/enterprise_document/services/scheme_writer/evidence_service.py",
            "rag.evidence.eligibility_policy",
        ),
    }
    hits: set[tuple[str, str]] = set()
    for path in _python_files(UPPER_ROOTS):
        targets = _static_import_targets(path) + _dynamic_literal_import_targets(path)
        for target in targets:
            if _is_package(target, "rag"):
                hits.add((_relative(path), target))
    assert hits == allowed


def test_rag_has_no_upward_dependency() -> None:
    hits: list[str] = []
    for path in _python_files((RAG_ROOT,)):
        targets = _static_import_targets(path) + _dynamic_literal_import_targets(path)
        for target in targets:
            if any(_is_package(target, package) for package in UPPER_PACKAGE_NAMES):
                hits.append(f"{_relative(path)} -> {target}")
    assert hits == []


def test_bootstrap_is_explicit_composition_root_exception() -> None:
    factory = BACKEND_ROOT / "bootstrap" / "supervisor_factory.py"
    targets = _static_import_targets(factory)
    assert any(_is_package(target, "rag") for target in targets)
    assert factory not in _python_files(UPPER_ROOTS)


def test_production_file_scanner_explicitly_excludes_colocated_tests(tmp_path: Path) -> None:
    application_root = tmp_path / "application"
    production = application_root / "service.py"
    colocated_test = application_root / "tests" / "test_arch.py"
    inline_test = application_root / "test_inline.py"
    suffix_test = application_root / "inline_test.py"
    conftest = application_root / "conftest.py"

    for path in (production, colocated_test, inline_test, suffix_test, conftest):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("import rag.internal\n", encoding="utf-8")

    assert _python_files((application_root,)) == [production]


def test_dynamic_import_scanner_handles_literal_alias_forms(tmp_path: Path) -> None:
    sample = tmp_path / "dynamic_imports.py"
    sample.write_text(
        "import importlib as il\n"
        "from importlib import import_module as load_module\n"
        "il.import_module('rag.internal')\n"
        "load_module('application.internal')\n"
        "__import__('agent.internal')\n",
        encoding="utf-8",
    )
    assert _dynamic_literal_import_targets(sample) == [
        "rag.internal",
        "application.internal",
        "agent.internal",
    ]


def test_static_import_scanner_counts_type_checking_dependency(tmp_path: Path) -> None:
    sample = tmp_path / "type_checking_dependency.py"
    sample.write_text(
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    import rag.type_only_dependency\n",
        encoding="utf-8",
    )
    assert "rag.type_only_dependency" in _static_import_targets(sample)


def test_static_import_scanner_ignores_docstrings_and_comments(tmp_path: Path) -> None:
    sample = tmp_path / "non_import_text.py"
    sample.write_text(
        '"""import rag.docstring_only"""\n'
        "# import rag.comment_only\n"
        "VALUE = 'import rag.string_only'\n",
        encoding="utf-8",
    )
    assert _static_import_targets(sample) == []


def test_static_import_scanner_detects_positive_production_import(tmp_path: Path) -> None:
    sample = tmp_path / "production_service.py"
    sample.write_text("import rag.internal\n", encoding="utf-8")
    assert _static_import_targets(sample) == ["rag.internal"]


def test_bootstrap_maps_runtime_mode_to_neutral_section_retrieval_default() -> None:
    factory = BACKEND_ROOT / "bootstrap" / "supervisor_factory.py"
    tree = ast.parse(factory.read_text(encoding="utf-8"), filename=str(factory))
    matching_keywords: list[ast.AST] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        call_name = _call_path(node.func)
        if call_name != "SchemeWriterAgent":
            continue
        matching_keywords.extend(
            keyword.value
            for keyword in node.keywords
            if keyword.arg == "section_retrieval_default_enabled"
        )
    assert len(matching_keywords) == 1
    value = matching_keywords[0]
    assert isinstance(value, ast.Attribute)
    assert value.attr == "use_real_rag"
    assert isinstance(value.value, ast.Name)
    assert value.value.id == "options"


def _matching_concrete_identity_values(
    node: ast.AST,
    values: set[str],
) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    for child in ast.walk(node):
        if (
            isinstance(child, ast.Constant)
            and isinstance(child.value, str)
            and child.value in values
        ):
            hits.append((getattr(child, "lineno", 0), child.value))
        elif isinstance(child, ast.Name) and child.id in values:
            hits.append((getattr(child, "lineno", 0), child.id))
        elif isinstance(child, ast.Attribute) and child.attr in values:
            hits.append((getattr(child, "lineno", 0), child.attr))
    return hits


def _concrete_rag_behavior_identity_hits(
    tree: ast.AST,
    values: set[str] = CONCRETE_RAG_IMPLEMENTATION_NAMES,
) -> list[tuple[int, str]]:
    hits: set[tuple[int, str]] = set()

    control_expressions: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.While, ast.IfExp)):
            control_expressions.append(node.test)
        elif isinstance(node, ast.comprehension):
            control_expressions.extend(node.ifs)

    for expression in control_expressions:
        for child in ast.walk(expression):
            if isinstance(child, ast.Compare):
                hits.update(_matching_concrete_identity_values(child, values))
            elif (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Name)
                and child.func.id == "isinstance"
                and len(child.args) >= 2
            ):
                hits.update(_matching_concrete_identity_values(child.args[1], values))

    for node in ast.walk(tree):
        if not isinstance(node, ast.Match):
            continue
        for case in node.cases:
            hits.update(_matching_concrete_identity_values(case.pattern, values))

    return sorted(hits)


def _concrete_rag_behavior_identity_hits_in_file(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return _concrete_rag_behavior_identity_hits(tree)


def test_application_behavior_does_not_branch_on_concrete_rag_service_name() -> None:
    hits: list[str] = []
    for path in _python_files(UPPER_ROOTS):
        for lineno, value in _concrete_rag_behavior_identity_hits_in_file(path):
            hits.append(f"{_relative(path)}:{lineno}:{value}")
    assert hits == []


@pytest.mark.parametrize(
    ("source", "expected_value"),
    [
        ("if rag_service_name == 'RAGService':\n    use_real()\n", "RAGService"),
        ("if rag_service_name != 'FakeRAGService':\n    use_real()\n", "FakeRAGService"),
        (
            "if rag_service_name in {'RAGService', 'FakeRAGService'}:\n    use_real()\n",
            "RAGService",
        ),
        ("if type(rag_service).__name__ == 'RAGService':\n    use_real()\n", "RAGService"),
        (
            "if type(rag_service).__name__ in {'RAGService', 'FakeRAGService'}:\n    use_real()\n",
            "FakeRAGService",
        ),
        (
            "if rag_service.__class__.__name__ == 'RAGService':\n    use_real()\n",
            "RAGService",
        ),
        ("if isinstance(rag_service, RAGService):\n    use_real()\n", "RAGService"),
        (
            "if isinstance(rag_service, FakeRAGService):\n    use_fake()\n",
            "FakeRAGService",
        ),
        (
            "match type(rag_service).__name__:\n"
            "    case 'RAGService':\n"
            "        use_real()\n",
            "RAGService",
        ),
        (
            "match rag_service.__class__.__name__:\n"
            "    case 'FakeRAGService':\n"
            "        use_fake()\n",
            "FakeRAGService",
        ),
    ],
    ids=[
        "name-equality",
        "name-inequality",
        "name-membership",
        "type-name-equality",
        "type-name-membership",
        "class-name-equality",
        "isinstance-real",
        "isinstance-fake",
        "match-type-name",
        "match-class-name",
    ],
)
def test_concrete_rag_identity_control_flow_synthetic_forms(
    source: str,
    expected_value: str,
) -> None:
    hits = _concrete_rag_behavior_identity_hits(ast.parse(source))
    assert expected_value in {value for _, value in hits}


def test_concrete_rag_identity_scanner_allows_observability_metadata() -> None:
    tree = ast.parse(
        "component = rag_service_name\n"
        "trace = {'component': rag_service_name, 'real_label': 'RAGService'}\n"
        "logger.info('FakeRAGService')\n"
    )
    assert _concrete_rag_behavior_identity_hits(tree) == []


class _ClosableDelegate:
    def __init__(self) -> None:
        self.close_count = 0
        self.inner = object()
        self.retrieval_runtime = object()

    def close(self) -> None:
        self.close_count += 1


def _facade_structural_violations(
    facade: LangChainRAGServiceFacade,
    *,
    full_delegate: object,
) -> list[str]:
    violations: list[str] = []

    instance_state = set(facade.__dict__)
    unexpected_state = instance_state - FACADE_ACCEPTED_INSTANCE_STATE
    if unexpected_state:
        violations.append(f"unexpected instance state: {sorted(unexpected_state)}")

    missing_state = FACADE_ACCEPTED_INSTANCE_STATE - instance_state
    if missing_state:
        violations.append(f"missing accepted instance state: {sorted(missing_state)}")

    for name, value in facade.__dict__.items():
        if value is full_delegate:
            violations.append(f"full delegate retained directly in instance state: {name}")

    public_surface = {
        name
        for name in LangChainRAGServiceFacade.__dict__
        if not name.startswith("_")
    }
    unexpected_public = public_surface - FACADE_ACCEPTED_PUBLIC_SURFACE
    if unexpected_public:
        violations.append(f"unexpected public surface: {sorted(unexpected_public)}")

    return violations


def test_rag_facade_has_explicit_closed_surface_and_lifecycle() -> None:
    delegate = _ClosableDelegate()
    facade = LangChainRAGServiceFacade(delegate=delegate, runnable=object())  # type: ignore[arg-type]

    assert "__getattr__" not in LangChainRAGServiceFacade.__dict__
    assert "close" in LangChainRAGServiceFacade.__dict__
    assert "retrieve" in LangChainRAGServiceFacade.__dict__
    assert set(facade.__dict__) == FACADE_ACCEPTED_INSTANCE_STATE
    assert _facade_structural_violations(facade, full_delegate=delegate) == []

    for name in FACADE_FORBIDDEN_TRAVERSAL_NAMES:
        assert name not in LangChainRAGServiceFacade.__dict__
        assert name not in facade.__dict__
        assert not hasattr(facade, name)

    facade.close()
    assert delegate.close_count == 1


@pytest.mark.parametrize("escape_name", ["backend", "service", "impl", "wrapped"])
def test_rag_facade_structural_guard_rejects_renamed_delegate_instance_state(
    escape_name: str,
) -> None:
    delegate = _ClosableDelegate()
    facade = LangChainRAGServiceFacade(delegate=delegate, runnable=object())  # type: ignore[arg-type]
    setattr(facade, escape_name, delegate)

    violations = _facade_structural_violations(facade, full_delegate=delegate)
    assert any("unexpected instance state" in violation for violation in violations)
    assert any("full delegate retained directly" in violation for violation in violations)


def test_rag_facade_structural_guard_rejects_renamed_public_delegate_property(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delegate = _ClosableDelegate()
    facade = LangChainRAGServiceFacade(delegate=delegate, runnable=object())  # type: ignore[arg-type]
    monkeypatch.setattr(
        LangChainRAGServiceFacade,
        "backend",
        property(lambda self: self._close.__self__),
        raising=False,
    )

    assert facade.backend is delegate  # type: ignore[attr-defined]
    violations = _facade_structural_violations(facade, full_delegate=delegate)
    assert any("unexpected public surface" in violation for violation in violations)


def test_rag_service_port_remains_retrieve_only_business_contract() -> None:
    public_methods = {
        name
        for name, value in RAGServicePort.__dict__.items()
        if callable(value) and not name.startswith("_")
    }
    assert public_methods == {"retrieve"}


def _rag_request() -> RAGToolInputSchema:
    return RAGToolInputSchema(
        task_id="task_phase7",
        run_id="run_phase7",
        agent_name="Phase7BoundaryTest",
        query="phase 7 retrieval contract",
        retrieval_trace_id="trace_phase7",
        need_citation=True,
        max_context_chars=6000,
        max_context_items=3,
    )


class _RaisingRuntime:
    def __init__(self, exc: BaseException) -> None:
        self.exc = exc

    def retrieve(self, payload):  # noqa: ANN001
        raise self.exc


class _RaisingFakeTool:
    tool_name = "Phase7RaisingFakeTool"

    def __init__(self, exc: BaseException) -> None:
        self.exc = exc

    def run(self, tool_call):  # noqa: ANN001
        raise self.exc


class _SuccessfulRuntime:
    def retrieve(self, payload):  # noqa: ANN001
        return {"ok": True}


class _RaisingResultMapper:
    def map(self, **kwargs):  # noqa: ANN003
        raise ValueError("result mapping failed")


class _InvalidOutputFakeTool:
    tool_name = "Phase7InvalidOutputFakeTool"

    def run(self, tool_call):  # noqa: ANN001
        return SimpleNamespace(result={"task_id": "invalid-only"})


def test_fake_and_real_normalize_exception_failures_to_same_public_lifecycle() -> None:
    request = _rag_request()
    real = RAGService(
        BACKEND_ROOT.parent,
        retrieval_runtime=_RaisingRuntime(RuntimeError("real failed")),
        allow_legacy_unscoped=True,
    )
    fake = FakeRAGService(tool=_RaisingFakeTool(RuntimeError("fake failed")))  # type: ignore[arg-type]

    real_bundle = real.retrieve(request)
    fake_bundle = fake.retrieve(request)

    for bundle in (real_bundle, fake_bundle):
        assert bundle.status is ExecutionStatus.FAILED
        assert bundle.retrieval_trace_id == "trace_phase7"
        assert bundle.error is not None
        assert bundle.error.error_code == "RAG_RETRIEVAL_FAILED"
        assert bundle.context.context_text == ""


def test_real_result_mapper_exception_is_permanently_normalized() -> None:
    bundle = RAGService(
        BACKEND_ROOT.parent,
        retrieval_runtime=_SuccessfulRuntime(),
        result_mapper=_RaisingResultMapper(),  # type: ignore[arg-type]
        allow_legacy_unscoped=True,
    ).retrieve(_rag_request())

    assert bundle.status is ExecutionStatus.FAILED
    assert bundle.error is not None
    assert bundle.error.error_code == "RAG_RETRIEVAL_FAILED"
    assert bundle.error.error_type == "ValueError"


def test_fake_tool_output_validation_exception_is_permanently_normalized() -> None:
    bundle = FakeRAGService(tool=_InvalidOutputFakeTool()).retrieve(_rag_request())  # type: ignore[arg-type]

    assert bundle.status is ExecutionStatus.FAILED
    assert bundle.error is not None
    assert bundle.error.error_code == "RAG_RETRIEVAL_FAILED"
    assert bundle.error.error_type == "ValidationError"


@pytest.mark.parametrize(
    "exc_factory",
    [
        lambda: ModelCallBudgetExceeded("budget exhausted"),
        lambda: WorkflowExecutionCancelled("cancelled"),
    ],
)
def test_fake_and_real_propagate_frozen_control_flow_exceptions(exc_factory) -> None:  # noqa: ANN001
    request = _rag_request()
    real_exc = exc_factory()
    fake_exc = exc_factory()
    real = RAGService(
        BACKEND_ROOT.parent,
        retrieval_runtime=_RaisingRuntime(real_exc),
        allow_legacy_unscoped=True,
    )
    fake = FakeRAGService(tool=_RaisingFakeTool(fake_exc))  # type: ignore[arg-type]

    with pytest.raises(type(real_exc)):
        real.retrieve(request)
    with pytest.raises(type(fake_exc)):
        fake.retrieve(request)


def _call_path(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_path(node.value)
        if parent:
            return f"{parent}.{node.attr}"
    return None


def _import_alias_map(path: Path, tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    aliases[alias.asname] = alias.name
        elif isinstance(node, ast.ImportFrom):
            module = _resolve_import_from(path, node)
            for alias in node.names:
                if alias.name == "*":
                    continue
                local = alias.asname or alias.name
                aliases[local] = f"{module}.{alias.name}" if module else alias.name
    return aliases


def _resolve_call_path(raw_path: str, aliases: dict[str, str]) -> str:
    first, dot, rest = raw_path.partition(".")
    resolved = aliases.get(first, first)
    return f"{resolved}.{rest}" if dot else resolved


def _is_canonical_evidence_constructor(call_path: str) -> bool:
    parts = call_path.split(".")
    if not parts:
        return False
    if parts[-1] == "build_evidence_bundle":
        return True
    if parts[-1] in {"EvidenceBundleSchema", "RAGEvidenceContractSchema"}:
        return True
    # `model_validate` on an already-typed/hash-bound canonical snapshot is an
    # integrity check, not a second Evidence producer. Step6/Step7 added several
    # exact-validation consumers while preserving the single producer boundary.
    return False


def _upper_canonical_evidence_calls() -> list[tuple[str, int, str]]:
    hits: list[tuple[str, int, str]] = []
    for path in _python_files(UPPER_ROOTS):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        aliases = _import_alias_map(path, tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            raw_path = _call_path(node.func)
            if not raw_path:
                continue
            resolved = _resolve_call_path(raw_path, aliases)
            if _is_canonical_evidence_constructor(resolved):
                hits.append((_relative(path), node.lineno, resolved))
    return hits


def test_legacy_evidence_adapter_is_sole_upper_canonical_evidence_constructor_ingress() -> None:
    hits = _upper_canonical_evidence_calls()
    assert hits
    assert {path for path, _, _ in hits} == {
        "apps/enterprise_document/services/scheme_writer/legacy_evidence_adapter.py"
    }
    assert any(call.endswith("build_evidence_bundle") for _, _, call in hits)


def test_evidence_constructor_guard_resolves_import_aliases() -> None:
    path = BACKEND_ROOT / "application" / "_phase7_synthetic.py"
    tree = ast.parse(
        "from schemas.rag import EvidenceBundleSchema as Bundle\n"
        "from contracts.evidence import build_evidence_bundle as build\n"
        "Bundle.model_validate({})\n"
        "build(query='q')\n"
    )
    aliases = _import_alias_map(path, tree)
    resolved_calls = {
        _resolve_call_path(raw, aliases)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for raw in [_call_path(node.func)]
        if raw is not None
    }
    assert "schemas.rag.EvidenceBundleSchema.model_validate" in resolved_calls
    assert "contracts.evidence.build_evidence_bundle" in resolved_calls
    assert _is_canonical_evidence_constructor(
        "contracts.evidence.build_evidence_bundle"
    )
    assert not _is_canonical_evidence_constructor(
        "schemas.rag.EvidenceBundleSchema.model_validate"
    )


def _definition_locations(kind: type[ast.AST], name: str) -> set[str]:
    locations: set[str] = set()
    for path in sorted(BACKEND_ROOT.rglob("*.py")):
        if _is_test_path(path):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, kind) and getattr(node, "name", None) == name:
                locations.add(_relative(path))
    return locations


def test_canonical_owner_locations_are_unique() -> None:
    assert _definition_locations(ast.ClassDef, "ClaimType") == {"schemas/claim.py"}
    assert _definition_locations(ast.ClassDef, "EvidenceStatus") == {
        "schemas/evidence_common.py"
    }
    assert _definition_locations(ast.ClassDef, "SelfRAGJudge") == {
        "apps/enterprise_document/quality/self_rag_judge.py"
    }
    assert _definition_locations(ast.ClassDef, "DeterministicTokenEstimator") == {
        "core/token_estimator.py"
    }
    assert _definition_locations(ast.ClassDef, "DocumentCatalogPort") == {
        "contracts/document_catalog.py"
    }
    assert _definition_locations(ast.ClassDef, "RAGServicePort") == {"contracts/rag.py"}
    assert _definition_locations(ast.FunctionDef, "build_evidence_bundle") == {
        "contracts/evidence.py"
    }
    assert _definition_locations(ast.FunctionDef, "project_evidence_bundle") == {
        "contracts/evidence.py"
    }
    assert _definition_locations(ast.FunctionDef, "evidence_bundle_sha256") == {
        "contracts/evidence.py"
    }


def _active_ast_nodes(path: Path) -> list[ast.AST]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        node
        for node in tree.body
        if not (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        )
    ]


def test_retired_owner_paths_remain_true_tombstones_and_no_token_alias() -> None:
    tombstones = (
        BACKEND_ROOT / "rag" / "evidence" / "contract.py",
        BACKEND_ROOT / "rag" / "judge" / "self_rag_judge.py",
        BACKEND_ROOT / "context_manager" / "token_estimator.py",
    )
    for path in tombstones:
        assert _active_ast_nodes(path) == []

    context_init = ast.parse(
        (BACKEND_ROOT / "context_manager" / "__init__.py").read_text(encoding="utf-8")
    )
    imported_names: set[str] = set()
    exported_names: set[str] = set()
    for node in context_init.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imported_names.update(alias.asname or alias.name for alias in node.names)
        elif (
            isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets)
            and isinstance(node.value, (ast.List, ast.Tuple))
        ):
            exported_names.update(
                element.value
                for element in node.value.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            )
    assert "DeterministicTokenEstimator" not in imported_names
    assert "DeterministicTokenEstimator" not in exported_names
