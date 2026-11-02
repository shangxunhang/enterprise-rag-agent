"""Bounded test-only native captures, never production/retry authority.

No native owner read/recovery is added by this helper. It invokes each original
observed method exactly once with unchanged inputs, records that actual return
or exception, and retains the original test oracle. Counts are only registered
ModelInvoker entries within this module's source-bound capture window; child or
direct custom handler calls require their independent native counter.
"""
import dataclasses
import functools
import hashlib
import importlib
import inspect
import json
import os
import threading
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def _plain(value, depth=0):
    if depth > 12:
        return {"status": "UNMEASURED", "reason": "NATIVE_CAPTURE_DEPTH_LIMIT"}
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, bytes):
        return {"actual_bytes_sha256": hashlib.sha256(value).hexdigest(), "actual_byte_count": len(value)}
    if isinstance(value, dict):
        return {str(key): _plain(item, depth + 1) for key, item in value.items()
                if isinstance(key, (str, int))}
    if isinstance(value, (tuple, list)):
        return [_plain(item, depth + 1) for item in value]
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {field.name: _plain(getattr(value, field.name), depth + 1)
                for field in dataclasses.fields(value)}
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return {"status": "UNMEASURED", "reason": "UNDECLARED_NATIVE_OBJECT_NOT_STRINGIFIED",
            "actual_type": type(value).__module__ + "." + type(value).__qualname__}


def _json_once(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    with path.open("xb") as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())


class NativeCapture:
    def __init__(self, declaration):
        self.declaration = declaration
        self.lock = threading.RLock()
        self.calls = []
        self.stack = ExitStack()
        self.installed = []

    def install(self, *, native_namespaces=()):
        for binding in self.declaration["hooks"]:
            path = (ROOT / binding["path"]).resolve()
            assert path.is_relative_to(ROOT) and path.is_file()
            assert hashlib.sha256(path.read_bytes()).hexdigest() == binding["sha256"]
            module = importlib.import_module(binding["module"])
            assert Path(module.__file__).resolve() == path
            owner = module
            parts = binding["symbol"].split(".")
            for part in parts[:-1]:
                owner = getattr(owner, part)
            name = parts[-1]
            descriptor = inspect.getattr_static(owner, name)
            assert not isinstance(descriptor, (classmethod, staticmethod)), "Descriptor wrappers require separate approved native binding"
            original = getattr(owner, name)
            assert inspect.isfunction(original) and not inspect.iscoroutinefunction(original)
            assert Path(original.__code__.co_filename).resolve() == path
            actual_signature = inspect.signature(original)
            plain_method = binding["plain_method"]
            if plain_method:
                assert next(iter(actual_signature.parameters), None) == "self"

            def observed(*args, _original=original, _binding=binding, **kwargs):
                event = {"source": _binding, "actual_pid": os.getpid(),
                         "actual_thread": threading.get_ident(),
                         "input_args_without_self": _plain(args[1:] if _binding["plain_method"] else args), "input_kwargs": _plain(kwargs)}
                with self.lock:
                    event["entry_index"] = len(self.calls)
                    self.calls.append(event)
                try:
                    actual = _original(*args, **kwargs)
                except BaseException as error:
                    with self.lock:
                        event["actual_exception"] = {"type": type(error).__name__, "message": str(error)}
                    raise
                else:
                    with self.lock:
                        event["actual_return"] = _plain(actual)
                    return actual
            observed = functools.wraps(original)(observed)
            assert inspect.signature(observed) == actual_signature
            self.stack.enter_context(patch.object(owner, name, observed))
            if not plain_method:
                # Exact actual imported function aliases in this new control and
                # its unchanged old helper module only; restored at case exit.
                for namespace in native_namespaces:
                    aliases = {key: observed for key, value in tuple(namespace.items()) if value is original}
                    if aliases:
                        self.stack.enter_context(patch.dict(namespace, aliases))
            self.installed.append(binding)
        return self

    def close(self):
        self.stack.close()

    def emit(self, *, root, rows, local_values, selector, native_source, native_local_names):
        with self.lock:
            calls = _plain(self.calls)
        values = {name: _plain(value) for name, value in local_values.items()
                  if name in self.declaration["explicit_local_names"]}
        providers = [event for event in calls if event["source"]["symbol"] == "ModelInvoker.invoke"]
        declared_native_locals = {name: values[name] for name in native_local_names if name in values
            and not (isinstance(values[name], dict) and values[name].get("status") == "UNMEASURED")}
        for row in rows:
            symbols = set(self.declaration["row_native_symbols"][row])
            row_calls = [event for event in calls if event["source"]["symbol"] in symbols]
            owner_results = [event for event in row_calls if "actual_return" in event
                and event["actual_return"] is not None
                and not (isinstance(event["actual_return"], dict) and event["actual_return"].get("status") == "UNMEASURED")]
            owner_errors = [event for event in row_calls if "actual_exception" in event]
            consumers = [event for event in calls if event["source"]["symbol"] in self.declaration["row_exact_final_consumer_symbols"][row]
                and "actual_return" in event]
            fields = {}
            def field(name, native_value, scope):
                fields[name] = {"records": [{"status": "MEASURED", "native_value": native_value,
                    "evidence_scope": scope, "native_point": native_source,
                    "selector": selector, "actual_pid": os.getpid()}]}
            if row_calls or declared_native_locals:
                field("exact_producer_input_material", {"direct_native_return_locals": declared_native_locals,
                    "actual_method_inputs": [{"source": event["source"], "args": event["input_args_without_self"],
                    "kwargs": event["input_kwargs"]} for event in row_calls]}, "ACTUALLY_REACHED_DECLARED_NATIVE_OWNER_INPUTS_AND_EXACT_NATIVE_FUNCTION_RETURN_LOCALS")
                field("actual_control_revisions", {"actual_native_inputs_returns": row_calls,
                    "direct_native_return_locals": declared_native_locals}, "ACTUAL_NATIVE_REFS_REVISIONS_OR_PURE_SOURCE_PROFILE_AS_RETURNED_NO_SYNTHETIC_RUN_CHILD")
            if owner_results or declared_native_locals:
                field("positive_exact_owner_result", {"actual_native_return_refs_and_bytes": owner_results,
                    "direct_declared_native_function_returns": declared_native_locals}, "ACTUAL_ACCEPTED_RETURN_VALUE_ONLY_NO_FIXTURE_LOCAL_OR_SUCCESS_FLAG_INFERENCE")
            if owner_errors:
                field("negative_mutation_and_rejection", {"actual_native_rejection_inputs_and_errors": owner_errors}, "ACTUAL_REACHED_NATIVE_EXCEPTION_CONTROL; NO_ERROR_ABSENCE_NEGATIVE_PROOF")
            if consumers:
                field("final_consumer_observation", consumers, "ONLY_RETURNED_EXACT_DECLARED_FINAL_CONSUMER_BOUNDARY; NO_PACKER_OR_GOVERNANCE_SUBSTITUTION")
            # This count is solely actually entered registered parent invoker
            # boundaries. Zero is never synthesized from installation/absence.
            # Independent authored handlers and child providers remain separate.
            if providers:
                field("physical_dispatch_count", len(providers), "ACTUAL_REGISTERED_PARENT_MODEL_INVOKER_ENTRIES_THIS_CASE_ONLY; OTHER_HANDLERS_AND_CHILD_FAMILIES_UNMEASURED")
            dispositions = {field_name: {"status": "UNMEASURED", "reason": "EXACT_DECLARED_NATIVE_BOUNDARY_NOT_REACHED_OR_NATIVE_VALUE_NOT_BOUND"}
                for field_name in self.declaration["generic_fields"] if field_name not in fields}
            dispositions["semantic_commit_delta"] = {"status": "UNMEASURED",
                "reason": "NATIVE_SEMANTIC_COMMIT_DELTA_REQUIRES_EXACT_OWNER_JOURNAL_OR_RECORD_REVISION_PROBE; METHOD_RETURN_NOT_APPEND_COUNT"}
            if row in self.declaration["pure_projection_rows"]:
                dispositions["semantic_commit_delta"] = {"status": "SOURCE_BOUND_N_A",
                    "reason": "EXACT_PURE_PROJECTION_CANONICAL_OWNER_HAS_NO_SEMANTIC_STORE_MUTATION; FINAL_CONSUMER_EFFECT_COUNTS_SEPARATE",
                    "evidence_scope": "PURE_PROJECTION_CANONICAL_OWNER_ONLY; FINAL_CONSUMER_ACCOUNTING_SEPARATE",
                    "source_decision": self.declaration["D5_source_decision"]}
            target = Path(root) / "native-row-observations" / row / "observation.json"
            _json_once(target, {"schema_version": "fpbca_explicit_native_field_observation_v1",
                "row": row, "selector": selector, "observations": fields,
                "field_dispositions": dispositions, "explicit_fixture_values": values,
                "actual_native_calls": calls, "actual_registered_parent_invoker_entries": providers, "registered_native_observer_hooks": self.installed,
                "scope": "BOUNDED_NEW_NATIVE_OBSERVER_OF_UNMODIFIED_OLD_TEST_FLOW",
                "quiescence": "UNMEASURED_UNLESS_ACTUAL_FIXTURE_RECORDED",
                "business_or_global_verdict": "NOT_GRANTED",
                "missing_consumer_is": "UNMEASURED_NO_COMPONENT_PASS_SUBSTITUTION"})
