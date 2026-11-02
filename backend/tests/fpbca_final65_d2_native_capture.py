"""Bounded native observations for unchanged local REC-W/T/Q/P/C/D controls.

Verification only. No owner retry, repair, controller transition, DB query or
business dispatch is introduced. Every observed original runs once with its
exact arguments. Unreached values remain UNMEASURED. File snapshots are explicit
test-owned diagnostic byte reads, not multi-file atomic snapshots.
"""
import dataclasses
from datetime import datetime
from enum import Enum
import functools
import hashlib
import importlib
import inspect
import json
import os
import sys
from pathlib import Path
import threading
import traceback
from contextlib import ExitStack
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
FIELDS = ("exact_native_operation_identity", "actual_cut_location",
    "native_intent_result_refs", "native_currentness_revisions",
    "actual_physical_dispatch_count", "actual_settlement_count",
    "native_cold_reopen_outcome", "exact_replay_outcome",
    "concurrent_duplicate_outcome")


def _plain(value, depth=0):
    if depth > 96:
        raise ValueError("closed native serializer depth exceeded")
    if value is None or isinstance(value, (str, bool, int, float)):
        json.dumps(value, allow_nan=False)
        return value
    if isinstance(value, (datetime, Path)):
        return value.isoformat() if isinstance(value, datetime) else str(value)
    if isinstance(value, Enum):
        return _plain(value.value, depth + 1)
    if isinstance(value, bytes):
        return {"sha256": hashlib.sha256(value).hexdigest(), "bytes": len(value), "hex": value.hex()}
    if isinstance(value, dict):
        if not all(isinstance(k, str) for k in value):
            raise ValueError("closed native serializer requires string keys")
        return {k: _plain(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(v, depth + 1) for v in value]
    if inspect.isfunction(value) or inspect.ismethod(value):
        function = value.__func__ if inspect.ismethod(value) else value
        path = Path(function.__code__.co_filename).resolve()
        if not path.is_relative_to(ROOT) or not path.is_file():
            raise ValueError("callback source outside reviewed project")
        return {"callback_source_identity_only": {"path": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "function": function.__qualname__, "line": function.__code__.co_firstlineno},
            "closure_state_and_permission": "UNMEASURED_NOT_INFERRED_FROM_CALLBACK_IDENTITY"}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _plain(getattr(value, f.name), depth + 1) for f in dataclasses.fields(value)}
    if hasattr(value, "model_dump"):
        return _plain(value.model_dump(mode="json", round_trip=True), depth + 1)
    raise ValueError("undeclared native serializer type: " + type(value).__module__ + "." + type(value).__qualname__)


def _complete(value):
    if isinstance(value, dict):
        return value.get("status") != "UNMEASURED" and all(_complete(v) for v in value.values())
    if isinstance(value, list):
        return all(_complete(v) for v in value)
    return True


def _error(error):
    return {"type": type(error).__name__, "message": str(error),
        "frames": [{"path": f.filename, "line": f.lineno, "function": f.name}
                   for f in traceback.extract_tb(error.__traceback__)]}


def _once(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


class RemainingNativeCapture:
    def __init__(self, declaration, root):
        self.declaration, self.root = declaration, Path(root).resolve()
        self.lock, self.stack = threading.RLock(), ExitStack()
        self.events, self.gaps, self.files, self.installations = [], [], {}, []
        self.flow_start_sequence = None

    def safe(self, value, phase):
        try:
            return _plain(value)
        except Exception as error:
            gap = {"phase": phase, "actual_error": _error(error),
                "actual_type": type(value).__module__ + "." + type(value).__qualname__}
            with self.lock:
                self.gaps.append(gap)
            return {"status": "UNMEASURED", "reason": "CLOSED_SERIALIZER_GAP", **gap}

    def _instance_material(self, instance, binding):
        result = {}
        for name in binding.get("instance_attributes", ()):
            # Only finite predeclared concrete attributes; no generic __dict__,
            # property probing, owner read call or repr conversion.
            value = vars(instance).get(name)
            if value is not None:
                result[name] = self.safe(value, "EXACT_DECLARED_NATIVE_ATTRIBUTE_" + name)
        return result

    def _snapshot(self, instance, binding, phase):
        if instance is None:
            return []
        snapshots = []
        for name in binding.get("owned_file_attributes", ()):
            value = vars(instance).get(name)
            if not isinstance(value, (str, Path)):
                continue
            path = Path(value).resolve()
            if not path.is_relative_to(self.root) or path.is_symlink():
                self.gaps.append({"phase": phase, "reason": "FILE_SNAPSHOT_OUTSIDE_FRESH_TEST_ROOT", "path": str(path)})
                continue
            try:
                raw = path.read_bytes() if path.is_file() else None
                item = {"path": str(path), "phase": phase, "exists": raw is not None,
                    "sha256": hashlib.sha256(raw).hexdigest() if raw is not None else None,
                    "bytes": len(raw) if raw is not None else None,
                    "hex": raw.hex() if raw is not None else None,
                    "consistency": "ACTUAL_SINGLE_FILE_BYTE_READ_NOT_CROSS_OWNER_ATOMIC"}
            except Exception as error:
                self.gaps.append({"phase": phase, "path": str(path), "actual_error": _error(error)})
                continue
            snapshots.append(item)
            if binding["kind"] != "EXACT_EIGHT_TRANSPORT_SUPPORT":
                with self.lock:
                    self.files.setdefault(str(path), []).append(item)
        return snapshots

    def _publication_constructor_projection(self, instance, binding):
        """Observe a finite same-process constructor subset, never full kwargs or authority."""
        assert binding["kind"] == "COLD_CONSTRUCTOR"
        assert binding["symbol"] == "PostWriteReleasePublicationService.__init__"
        assert binding["module"] == "apps.enterprise_document.services.post_write.release"
        assert binding["constructor_projection"] == "PUBLICATION_EXISTING_COMPONENT_IDENTITIES_V1"
        def exact(value, module, name):
            assert type(value).__module__ == module and type(value).__qualname__ == name
            source_binding = binding["constructor_projection_source_bindings"][name]
            source = Path(source_binding["path"])
            assert source.is_absolute() and source.is_relative_to(ROOT)
            for ancestor in (source, *source.parents):
                assert not ancestor.is_symlink() and not getattr(ancestor.lstat(), "st_file_attributes", 0) & 0x400
            assert source == Path(inspect.getfile(type(value))).resolve()
            assert hashlib.sha256(source.read_bytes()).hexdigest() == source_binding["sha256"]
            return {"module": module, "class": name, "pid": os.getpid(), "object_id": id(value),
                "actual_source_class": source_binding}
        release_module = "apps.enterprise_document.services.post_write.release"
        persistence_module = "apps.enterprise_document.services.post_write.persistence"
        store_module = "apps.enterprise_document.services.agent_rag_v1.artifact_store"
        owner_identity = exact(instance, release_module, "PostWriteReleasePublicationService")
        # Only these explicitly named concrete attributes, no generic object dump or owner call.
        store = vars(instance)["publication_store"]
        store_identity = exact(store, store_module, "FileArtifactStore")
        store_root = vars(store)["root"]
        assert isinstance(store_root, Path) and store_root.is_absolute()
        assert store_root.is_relative_to(self.root), "PUBLICATION_STORAGE_OUTSIDE_FRESH_TEST_ROOT"
        guard = vars(instance)["input_guard"]
        guard_identity = exact(guard, release_module, "PostWriteReleaseInputIntegrityGuard")
        fence = vars(instance)["release_commit_fence"]
        fence_identity = exact(fence, persistence_module, "PostWriteReleaseCommitFence")
        database = vars(fence)["database"]
        database_identity = exact(database, persistence_module, "PostWriteSqliteDatabase")
        database_path = vars(database)["path"]
        assert isinstance(database_path, Path)
        database_path = database_path.absolute()
        assert database_path.is_relative_to(self.root), "PUBLICATION_DATABASE_OUTSIDE_FRESH_TEST_ROOT"
        publisher = vars(instance)["publisher"]
        contract = vars(instance)["release_contract_version"]
        assert isinstance(publisher, str) and publisher and isinstance(contract, str) and contract
        clock = vars(instance)["clock"]
        assert inspect.isfunction(clock) or inspect.ismethod(clock), "UNDECLARED_PUBLICATION_CLOCK_IDENTITY"
        clock_identity = {"pid": os.getpid(), "object_id": id(clock), "source_identity": _plain(clock)}
        return {"schema_version": "fpbca_publication_constructor_subset_observation_v1",
            "native_owner_identity": owner_identity, "native_store_identity": store_identity,
            "native_fence_identity": fence_identity,
            "same_storage_and_component_input_key": {"publication_store_root": str(store_root),
                "input_guard_identity": guard_identity, "commit_fence_database_identity": database_identity,
                "commit_fence_database_path": str(database_path), "publisher": publisher,
                "release_contract_version": contract, "clock_callable_identity": clock_identity},
            "scope": "ACTUAL_SUCCESSFUL_SAME_PROCESS_CONSTRUCTOR_EFFECTIVE_STORAGE_AND_COMPONENT_IDENTITIES_ONLY",
            "full_kwargs_equality": "UNMEASURED_UNKNOWN_OBJECT_DIAGNOSTICS_RETAINED",
            "fault_injector": "OUTSIDE_PROJECTED_KEY_TEST_OWNED_RESPONSE_CUT_ONLY_NO_PERMISSION_INFERENCE",
            "not_claimed": ["FULL_CONSTRUCTOR_KWARGS_EQUAL", "OS_RESTART", "SQL_READ", "CURRENTNESS_PERMISSION", "BUSINESS_ACCEPTANCE"]}

    def _remaining_transport_hooks(self):
        """Transparent supporting hooks for exactly the reviewed C/D families."""
        source = self.declaration.get("original_source", {})
        path = Path(source.get("path", "")).absolute()
        for ancestor in (path, *path.parents):
            assert not ancestor.is_symlink() and not getattr(ancestor.lstat(), "st_file_attributes", 0) & 0x400
        keys = (self.declaration.get("row"), path.name, source.get("sha256"))
        scopes = {
            ("D2/REC-C", "test_full_chain_eval_capture.py", "d7ce4772d1ed2ea51ec90d98c38e4657d70450dda31bf0c47247fd220310586a"): [
                ("backend/core/diagnostics.py", "1ec245be5921729097f23d6d0302211ea1a77be36c86bfb8ae6436349e1fb5ea", "core.diagnostics", "DiagnosticSession.__exit__", 249)],
            ("D2/REC-C", "test_p1_security_durability_identity_closure.py", "8cfe0329d7059ff8c5acee17aeb1c760ea2b685ff2e132ed043363c4c2510ba8"): [
                ("backend/core/persistence/crash_safe_jsonl.py", "d6d8423ce733e3c85fdb2ac04b52ef3fbf9b584e27d2bdbe3f820c07b28d8401", "core.persistence.crash_safe_jsonl", "CrashSafeJsonlRecordSink._read_records_unlocked", 117)],
            ("D2/REC-D", "test_multi_agent_v3_memory_runtime.py", "dc971c616400a992d23fdd53f132edeba307be3df5d7883e4c6375d01661ab97"): [
                ("backend/apps/enterprise_document/services/agent_rag_v1/memory_runtime.py", "eaf1c37234a16e897ec2ecaf9a1c969ba9732681449f223a3a4cb0acc2b426b4", "apps.enterprise_document.services.agent_rag_v1.memory_runtime", "DurableMemoryRuntime._resolve_projection_sources", 472),
                ("backend/apps/enterprise_document/services/agent_rag_v1/memory_runtime.py", "eaf1c37234a16e897ec2ecaf9a1c969ba9732681449f223a3a4cb0acc2b426b4", "apps.enterprise_document.services.agent_rag_v1.memory_runtime", "DurableMemoryRuntime.project_source", 748)],
        }
        matched = scopes.get(keys, ())
        if matched:
            assert path == ROOT / "backend/tests" / path.name
            assert hashlib.sha256(path.read_bytes()).hexdigest() == source["sha256"]
        return [{"path": path, "sha256": digest, "module": module, "symbol": symbol,
            "line": line, "kind": "EXACT_EIGHT_TRANSPORT_SUPPORT", "plain_method": True,
            "instance_attributes": [], "owned_file_attributes": ["path"] if symbol.startswith("CrashSafeJsonl") else [],
            "received_exception_argument": symbol == "DiagnosticSession.__exit__"}
            for path, digest, module, symbol, line in matched]

    def _memory_constructor_projection(self, instance):
        """Finite successful same-process subset; original full kwargs stay unknown."""
        sources = {
            "TargetDurableMemoryComposition": ("memory_composition", "e64dffb3a14f53db7a31061574f5895b2518f393cd1f35d54c87c2a47b67468c"),
            "DurableMemoryRuntime": ("memory_runtime", "eaf1c37234a16e897ec2ecaf9a1c969ba9732681449f223a3a4cb0acc2b426b4"),
            "PostgresDurableMemoryStore": ("memory_store", "d5a91efce45aa588e1ecf2febb7986dee5d9981a249b353fdc9cc777c5281b7b"),
            "FactSnapshotRuntime": ("fact_snapshot_runtime", "b59e719607711751c0651010d8b26ef148a4c8dbc682046c56e704931c1c3ee9"),
            "RunAttemptLifecycleService": ("lifecycle_runtime", "1f0d5399b7fc1edd0addf1f2dc06e1d1790ad6b944ae13a960694572f858c169"),
            "ArtifactRuntime": ("artifact_runtime", "ad2d0f12b4774fa52d2b583516099aaf0338770be4bc6ad0ef1f254d5a146f3f"),
        }
        def exact(value, name):
            module_name, digest = sources[name]
            module = "apps.enterprise_document.services.agent_rag_v1." + module_name
            assert type(value).__module__ == module and type(value).__qualname__ == name
            path = ROOT / "backend/apps/enterprise_document/services/agent_rag_v1" / (module_name + ".py")
            for ancestor in (path, *path.parents):
                assert not ancestor.is_symlink() and not getattr(ancestor.lstat(), "st_file_attributes", 0) & 0x400
            assert path == Path(inspect.getfile(type(value))).resolve()
            assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
            return {"module": module, "class": name, "pid": os.getpid(), "object_id": id(value),
                "actual_source_class": {"path": str(path), "sha256": digest}}
        declared_path_presence = []
        def owned_path(value):
            assert isinstance(value, Path) and value.is_absolute()
            assert value == value.absolute() and value.is_relative_to(self.root), "MEMORY_SOURCE_PATH_OUTSIDE_FRESH_TEST_ROOT"
            assert self.root == self.root.absolute()
            assert ".." not in value.parts and "." not in value.parts
            leaf_exists = None
            for ancestor in (value, *value.parents):
                assert not ancestor.is_symlink()
                try:
                    attrs = ancestor.lstat()
                except FileNotFoundError:
                    if ancestor == value:
                        leaf_exists = False
                    continue  # A constructor locator is not a claim that the log exists.
                if ancestor == value:
                    leaf_exists = True
                assert not getattr(attrs, "st_file_attributes", 0) & 0x400
            declared_path_presence.append({"path": str(value), "exists_at_constructor_return": leaf_exists,
                "scope": "ACTUAL_LEXICAL_PATH_PRESENCE_ONLY_NOT_CONTENT_COMMIT_OR_CURRENTNESS"})
            return str(value)
        owner = exact(instance, "TargetDurableMemoryComposition")
        attrs = vars(instance)
        assert attrs["required"] is True and attrs["research"] is None and attrs["claims"] is None
        runtime, facts, lifecycle, artifacts = (attrs[key] for key in ("runtime", "facts", "lifecycle", "artifacts"))
        runtime_identity = exact(runtime, "DurableMemoryRuntime")
        store = vars(runtime)["store"]
        store_identity = exact(store, "PostgresDurableMemoryStore")
        fact_identity, life_identity, artifact_identity = (exact(facts, "FactSnapshotRuntime"),
            exact(lifecycle, "RunAttemptLifecycleService"), exact(artifacts, "ArtifactRuntime"))
        assert vars(facts)["artifact_runtime"] is artifacts and vars(artifacts)["lifecycle_service"] is lifecycle
        store_attrs = vars(store)
        assert store_attrs["database_url"] == ""
        connection = store_attrs["connection_factory"]
        assert inspect.isfunction(connection) or inspect.ismethod(connection)
        assert type(store_attrs["statement_timeout_ms"]) is int and isinstance(store_attrs["schema_version"], str)
        return {"schema_version": "fpbca_memory_constructor_subset_observation_v1",
            "native_owner_identity": owner, "native_runtime_identity": runtime_identity,
            "same_storage_and_component_input_key": {"required": True, "store_identity": store_identity,
                "fact_identity": fact_identity, "lifecycle_identity": life_identity, "artifact_identity": artifact_identity,
                "lifecycle_path": owned_path(vars(lifecycle)["path"]),
                "artifact_ledger_path": owned_path(vars(artifacts)["ledger_path"]),
                "statement_timeout_ms": store_attrs["statement_timeout_ms"], "schema_version": store_attrs["schema_version"],
                "connection_factory_identity": {"pid": os.getpid(), "object_id": id(connection), "source_identity": _plain(connection)}},
            "declared_source_path_presence": declared_path_presence,
            "scope": "ACTUAL_SUCCESSFUL_SAME_PROCESS_CONSTRUCTOR_SOURCE_AND_COMPONENT_SUBSET_ONLY",
            "full_kwargs_equality": "UNMEASURED_UNKNOWN_OBJECT_DIAGNOSTICS_RETAINED",
            "not_claimed": ["FULL_KWARGS_EQUALITY", "OS_RESTART", "LIVE_POSTGRES", "SOURCE_CURRENTNESS_PERMISSION", "BUSINESS_ACCEPTANCE"]}


    def install(self, *, namespaces=()):
        for binding in [*self.declaration["hooks"], *self._remaining_transport_hooks()]:
            path = (ROOT / binding["path"]).resolve()
            assert path.is_relative_to(ROOT) and path.is_file() and not path.is_symlink()
            assert hashlib.sha256(path.read_bytes()).hexdigest() == binding["sha256"]
            module = importlib.import_module(binding["module"])
            assert Path(module.__file__).resolve() == path
            owner = module
            components = binding["symbol"].split(".")
            for component in components[:-1]:
                owner = getattr(owner, component)
            name = components[-1]
            descriptor = inspect.getattr_static(owner, name)
            assert not isinstance(descriptor, (classmethod, staticmethod)), "descriptor needs a separately approved native observer"
            original = getattr(owner, name)
            assert inspect.isfunction(original) and not inspect.iscoroutinefunction(original)
            assert Path(original.__code__.co_filename).resolve() == path
            assert original.__code__.co_firstlineno == binding["line"]
            signature = inspect.signature(original)
            method = binding["plain_method"]
            if method:
                assert next(iter(signature.parameters), None) == "self"

            def observed(*args, _original=original, _binding=binding, **kwargs):
                instance = args[0] if _binding["plain_method"] and args else None
                event = {"native_point": {"path": str((ROOT / _binding["path"]).resolve()),
                    "sha256": _binding["sha256"], "function": _binding["symbol"], "line": _binding["line"],
                    "native_operation": _binding["kind"], "native_refs": []},
                    "kind": _binding["kind"], "pid": os.getpid(), "thread_id": threading.get_ident(),
                    "native_instance_id": id(instance) if instance is not None else None,
                    "args": self.safe(args[1:] if _binding["plain_method"] else args, "NATIVE_ARGUMENTS"),
                    "kwargs": self.safe(kwargs, "NATIVE_KEYWORD_ARGUMENTS")}
                if _binding.get("received_exception_argument"):
                    try:
                        received = inspect.signature(_original).bind(*args, **kwargs).arguments.get("exc")
                        if isinstance(received, BaseException):
                            event["actual_received_exception"] = _error(received)
                            event["received_exception_scope"] = "ACTUAL_EXISTING_CONTEXT_EXIT_ARGUMENT; NOT_EXCEPTION_RAISED_BY_EXIT"
                    except Exception as gap:
                        self.gaps.append({"phase": "SUPPORTING_EXIT_METADATA_ONLY", "actual_error": _error(gap)})
                        event["received_exception_metadata"] = {"status": "UNMEASURED", "reason": "ACTUAL_EXCEPTION_ARGUMENT_OBSERVER_GAP"}
                if instance is not None and _binding["kind"] != "COLD_CONSTRUCTOR":
                    try:
                        event["instance_before"] = self._instance_material(instance, _binding)
                        event["files_before"] = self._snapshot(instance, _binding, "BEFORE_EXACT_NATIVE_CALL")
                    except Exception as gap:
                        self.gaps.append({"phase":"PRE_NATIVE_METADATA_ONLY","actual_error":_error(gap)})
                        event["metadata_before"]={"status":"UNMEASURED","reason":"ACTUAL_METADATA_GAP"}
                with self.lock:
                    event["sequence"] = len(self.events)
                    self.events.append(event)
                # The capture mutex is deliberately released across native code.
                try:
                    result = _original(*args, **kwargs)
                except BaseException as error:
                    event["actual_error"] = _error(error)
                    self._flush_event(event)
                    raise
                else:
                    event["actual_return"] = self.safe(result, "NATIVE_RETURN")
                    if instance is not None:
                        try:
                            event["instance_after"] = self._instance_material(instance, _binding)
                            event["files_after"] = self._snapshot(instance, _binding, "AFTER_EXACT_NATIVE_CALL")
                            if _binding.get("constructor_projection") is not None:
                                event["constructor_subset_projection"] = self._publication_constructor_projection(instance, _binding)
                            if (_binding["symbol"] == "TargetDurableMemoryComposition.__init__"
                                    and self.declaration.get("row") == "D2/REC-D"
                                    and self.declaration.get("original_source", {}).get("sha256") == "7507f2af7b3f3e980695ae78895a9e9bf1409e14984c6588767679b3ee52b88f"):
                                event["memory_constructor_subset_projection"] = self._memory_constructor_projection(instance)
                        except Exception as gap:
                            self.gaps.append({"phase":"POST_NATIVE_METADATA_ONLY","actual_error":_error(gap)})
                            event["metadata_after"]={"status":"UNMEASURED","reason":"ACTUAL_METADATA_GAP"}
                    self._flush_event(event)
                    return result

            observed = functools.wraps(original)(observed)
            assert inspect.signature(observed) == signature
            self.stack.enter_context(patch.object(owner, name, observed))
            if not method:
                for namespace in namespaces:
                    aliases = {key: observed for key, value in tuple(namespace.items()) if value is original}
                    if aliases:
                        self.stack.enter_context(patch.dict(namespace, aliases))
            self.installations.append(binding)
        return self

    def _flush_event(self, event):
        # A test-owned diagnostic write failure cannot replace the actual owner
        # result or native exception. Finalization reports the observer gap.
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            encoded = json.dumps(event, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"
            with self.lock:
                with (self.root / "actual-d2-owner-events.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
        except Exception as error:
            with self.lock:
                self.gaps.append({"phase": "DURABLE_DIAGNOSTIC_EVENT", "actual_error": _error(error)})

    def close(self):
        self.stack.close()

    def begin_flow(self):
        with self.lock:
            self.flow_start_sequence = len(self.events)

    def safe_emit(self, **kwargs):
        primary=sys.exc_info()[1]
        try:
            return self.emit(**kwargs)
        except Exception as gap:
            if primary is None:
                raise
            try:
                _once(self.root / "native-observation-incomplete.json", {"status":"UNMEASURED_OBSERVER_FAILED",
                    "original_test_error":_error(primary),"actual_emission_error":_error(gap)})
            except Exception:
                pass
            # The surrounding finally retains its original exception.
            return None

    def _publication_replay_observation(self, *, row, events, flow, original_point):
        """Observe exact native custody/typed return; never call a native owner."""
        marker = "PUBLICATION_NATIVE_MANIFEST_CUSTODY_AND_TYPED_REPLAY_V1"
        if row != "D2/REC-P" or flow.get("publication_replay_observation") != marker:
            return None
        source = self.declaration.get("original_source", {})
        locations = {
            "test_target_native_cut_independent_cold_owner_counts_and_exact_bytes": 42,
            "test_two_independent_publishers_share_native_lock_and_single_manifest": 99,
        }
        if (original_point.get("path") != source.get("path")
                or original_point.get("sha256") != source.get("sha256")
                or original_point.get("function") not in locations
                or original_point.get("line") != locations[original_point["function"]]
                or flow.get("replay_symbols") != ["PostWriteReleasePublicationService.publish"]):
            return None
        hooks = {hook["symbol"]: hook for hook in self.declaration["hooks"]}

        def reached(event, symbol):
            hook = hooks.get(symbol)
            point = event.get("native_point", {})
            return (isinstance(hook, dict) and point.get("function") == symbol
                and point.get("path") == str(ROOT / hook["path"])
                and point.get("sha256") == hook["sha256"]
                and point.get("line") == hook["line"]
                and point.get("native_operation") == hook["kind"])

        try:
            sequences = [event["sequence"] for event in events]
            if (not sequences or any(type(value) is not int for value in sequences)
                    or len(sequences) != len(set(sequences))):
                return None
            constructor_hook = hooks["PostWriteReleasePublicationService.__init__"]
            if constructor_hook.get("constructor_projection") != "PUBLICATION_EXISTING_COMPONENT_IDENTITIES_V1":
                return None
            projections = []
            for event in events:
                if not reached(event, "PostWriteReleasePublicationService.__init__"):
                    continue
                value = event.get("constructor_subset_projection")
                if (not isinstance(value, dict) or not _complete(value)
                        or value.get("schema_version") != "fpbca_publication_constructor_subset_observation_v1"):
                    return None
                owner = value["native_owner_identity"]
                store = value["native_store_identity"]
                if (type(owner["object_id"]) is not int or type(store["object_id"]) is not int
                        or owner["object_id"] != event["native_instance_id"]
                        or owner["pid"] != event["pid"] or store["pid"] != event["pid"]
                        or owner["class"] != "PostWriteReleasePublicationService"
                        or store["class"] != "FileArtifactStore"):
                    return None
                for identity in (owner, store):
                    if identity["actual_source_class"] != constructor_hook[
                            "constructor_projection_source_bindings"][identity["class"]]:
                        return None
                projections.append(event)
            projections.sort(key=lambda event: event["sequence"])
            if len(projections) < 2:
                return None
            key = projections[0]["constructor_subset_projection"]["same_storage_and_component_input_key"]
            if any(event["constructor_subset_projection"]["same_storage_and_component_input_key"] != key
                    for event in projections):
                return None
            owners = [event["constructor_subset_projection"]["native_owner_identity"]["object_id"] for event in projections]
            stores = [event["constructor_subset_projection"]["native_store_identity"]["object_id"] for event in projections]
            if len(set(owners)) != len(owners) or len(set(stores)) != len(stores):
                return None
            cold_by_owner = {owners[i]: projections[i] for i in range(1, len(projections))}
            calls = [event for event in events if reached(event, "PostWriteReleasePublicationService.publish")]
            getters = [event for event in events if reached(event, "FileArtifactStore.get_json")]
            puts = [event for event in events if reached(event, "FileArtifactStore.put_bytes")]
            request_fields = ("request_sha256", "task_id", "run_id", "document_logical_id",
                "document_artifact_ref", "post_write_control_decision_ref", "publication_target_id",
                "output_format", "release_contract_version", "validated_output_binding_sha256")
            typed_replays = []
            for event in calls:
                ctor = cold_by_owner.get(event["native_instance_id"])
                if ctor is None or event["sequence"] <= ctor["sequence"]:
                    continue
                result = event.get("actual_return")
                if not isinstance(result, dict) or result.get("reuse_state") != "EXACT_REQUEST_REPLAY":
                    continue
                request = event["kwargs"]["request"]
                manifest = result["manifest"]
                if (result.get("schema_version") != "post_write_published_release_v1"
                        or request.get("schema_version") != "post_write_release_request_v1"
                        or manifest.get("schema_version") != "post_write_release_manifest_v1"
                        or not _complete(result)
                        or any(manifest[field] != request[field] for field in request_fields)
                        or manifest["run_completion_assessment_ref"] != event["kwargs"]["run_completion_assessment_ref"]
                        or manifest["run_lifecycle_revision"] != event["kwargs"]["run_lifecycle_revision"]):
                    return None
                reference = result["manifest_artifact"]
                manifest_ref = "target-release://" + manifest["idempotency_key"] + "/manifest"
                if (reference["artifact_ref"] != manifest_ref
                        or reference["artifact_type"] != "post_write_release_manifest"
                        or reference["task_id"] != request["task_id"] or reference["run_id"] != request["run_id"]):
                    return None
                store_id = ctor["constructor_subset_projection"]["native_store_identity"]["object_id"]
                reads = [read for read in getters if read["native_instance_id"] == store_id
                    and read["sequence"] > ctor["sequence"] and read.get("args") == [manifest_ref]
                    and read.get("kwargs", {}).get("task_id") == request["task_id"]
                    and read.get("actual_return") == manifest]
                if not reads:
                    return None
                typed_replays.append({"native_constructor": ctor, "actual_native_publish": event,
                    "actual_native_manifest_getters": reads,
                    "exact_native_request_manifest_projection": {field: request[field] for field in request_fields},
                    "outcome": result["reuse_state"],
                    "callback_closure_and_full_kwargs_equality": "UNMEASURED_NOT_REQUIRED_OR_INFERRED_FROM_NATIVE_REUSE_STATE"})
            if typed_replays:
                return {"scope": "ACTUAL_NATIVE_TYPED_EXACT_REQUEST_REPLAY_RETURN_AND_COMMITTED_MANIFEST_READ",
                    "actual_typed_replay_outcomes": typed_replays, "original_native_control": original_point}
            if original_point["function"] != "test_target_native_cut_independent_cold_owner_counts_and_exact_bytes":
                return None
            negatives = []
            for owner_id, ctor in cold_by_owner.items():
                store_id = ctor["constructor_subset_projection"]["native_store_identity"]["object_id"]
                after_calls = [event for event in calls if event["native_instance_id"] == owner_id
                    and event["sequence"] > ctor["sequence"]]
                after_puts = [event for event in puts if event["native_instance_id"] == store_id
                    and event["sequence"] > ctor["sequence"]]
                if after_calls or after_puts:
                    return None
                for read in getters:
                    if (read["native_instance_id"] != store_id or read["sequence"] <= ctor["sequence"]
                            or read.get("actual_error", {}).get("type") != "FileNotFoundError"
                            or len(read.get("args", [])) != 1 or not read["args"][0].endswith("/manifest")):
                        continue
                    manifest_ref = read["args"][0]
                    prefix = manifest_ref.removesuffix("/manifest") + "/"
                    prior = [event for event in calls if event["sequence"] < ctor["sequence"]
                        and event.get("kwargs", {}).get("request", {}).get("task_id") == read["kwargs"].get("task_id")
                        and event.get("kwargs", {}).get("request", {}).get("schema_version") == "post_write_release_request_v1"]
                    preceding_puts = [event for event in puts if event["sequence"] < ctor["sequence"]
                        and event["native_instance_id"] == stores[0]
                        and str(event.get("kwargs", {}).get("artifact_ref", "")).startswith(prefix)]
                    if len(prior) != 1 or not preceding_puts:
                        return None
                    negatives.append({"outcome": "NOT_REPLAYED_MISSING_COMMITTED_MANIFEST",
                        "native_constructor": ctor, "actual_native_manifest_getter": read,
                        "actual_prior_publication_entry": prior[0], "actual_prior_store_put_entries": preceding_puts,
                        "observed_cold_publication_entries": after_calls, "observed_cold_store_put_entries": after_puts,
                        "native_observation_window": {"first_sequence": min(sequences), "last_sequence": max(sequences)},
                        "replay_success": False, "new_publication_permission": "NOT_GRANTED"})
            if negatives:
                return {"scope": "ACTUAL_NEGATIVE_COLD_MANIFEST_CUSTODY_NO_REPLAY_OR_PUBLISH_ATTEMPT_IN_CAPTURED_CONTROL_WINDOW",
                    "actual_negative_custody_outcomes": negatives, "original_native_control": original_point}
        except (KeyError, IndexError, TypeError, ValueError):
            return None
        return None


    def _tool_passive_receipt_observation(self, *, row, flow, original_point):
        # Closed transport of an already-persisted native control receipt. This
        # never invokes/reopens a Tool owner and never grants replay permission.
        if (row != "D2/REC-Q" or flow.get("native_tool_passive_receipt") !=
                "TOOL_NATIVE_EXACT_FINAL_RECEIPT_CUSTODY_V1"):
            return None
        source = self.declaration.get("original_source", {})
        functions = {
            "test_actual_tool_loss_cuts_cold_canceled_passive",
            "test_passive_original_producer_binding_drift_is_indeterminate",
            "test_passive_full_native_tuple_drift_is_indeterminate",
            "test_passive_source_absence_or_corruption_is_indeterminate",
            "test_q1_active_replay_counts_real_projection_persist_separately",
            "test_q2_native_non_success_disposition_is_retained",
            "test_actual_two_gateway_native_miss_concurrent_observer_counts",
            "test_tool_native_torn_prefix_and_complete_frame_controls",
        }
        if (original_point.get("function") not in functions
                or original_point.get("path") != source.get("path")
                or original_point.get("sha256") != source.get("sha256")):
            return None
        try:
            source_path = Path(source["path"])
            receipt_path = self.root / "tool-native-observation-final.json"
            for path in (source_path, receipt_path):
                for ancestor in (path, *path.parents):
                    st = ancestor.lstat()
                    if ancestor.is_symlink() or getattr(st, "st_file_attributes", 0) & 0x400:
                        return None
                if not path.is_file() or path.resolve() != path:
                    return None
            if (not source_path.is_relative_to(ROOT)
                    or hashlib.sha256(source_path.read_bytes()).hexdigest() != source["sha256"]):
                return None
            raw = receipt_path.read_bytes()
            body = json.loads(raw.decode("utf-8"))
            if (not isinstance(body, dict)
                    or body.get("classification") != "LOCAL_AUTHORED_BACKEND_ACTUAL_NATIVE_OWNERS_TEST_EVIDENCE"
                    or body.get("formal_D2_row_verdict") != "PARENT_FROZEN_RECEIPT_REQUIRED"
                    or body.get("registration") not in {"mainline_evidence_service_v1", "standalone-v1"}
                    or not isinstance(body.get("original_origin"), dict)
                    or not isinstance(body.get("observation"), dict)
                    or not isinstance(body.get("counts"), dict)
                    or not isinstance(body.get("producer_proofs"), list)):
                return None
            origin = json.dumps(body["original_origin"], ensure_ascii=False,
                sort_keys=True, separators=(",", ":")).encode("utf-8")
            if hashlib.sha256(origin).hexdigest() != body.get("original_origin_sha256"):
                return None
            for phase in ("before", "after"):
                if not isinstance(body.get(phase), dict):
                    return None
                for item in body[phase].values():
                    if (not isinstance(item, dict) or not isinstance(item.get("hex"), str)
                            or hashlib.sha256(bytes.fromhex(item["hex"])).hexdigest() != item.get("sha256")):
                        return None
            # Only exact existing native custody/outcome values are forwarded.
            # The authored provider/model zero constants are deliberately absent.
            return {
                "actual_existing_native_final_receipt": {"path": str(receipt_path),
                    "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)},
                "actual_native_control_source": original_point,
                "original_origin": body["original_origin"],
                "original_origin_sha256": body["original_origin_sha256"],
                "registration": body["registration"], "actual_control_cut": body["cut"],
                "actual_bounded_native_observation": body["observation"],
                "actual_native_counter_inventory": body["counts"],
                "actual_source_proof_receipts": body["producer_proofs"],
                "actual_before_bytes": body["before"], "actual_after_bytes": body["after"],
                "filesystem_note": body["filesystem_note"],
                "replay_or_dispatch_permission": "NOT_GRANTED",
                "scope": "EXACT_SOURCE_BOUND_EXISTING_TEST_OWNED_PASSIVE_CUSTODY_OR_ACTIVE_ALIAS_OBSERVATION_NOT_PRODUCTION_TOOL_RECOVERY",
                "authored_zero_fields_are_measurements": False,
            }
        except (OSError, ValueError, KeyError, TypeError):
            # Missing, malformed, conflicting or unsafe observation is unknown;
            # no repair, fallback receipt, new native read or effect is allowed.
            return None

    def emit(self, *, row, selector, local_values, original_point, flow):
        events = self.safe(self.events, "FINAL_EVENT_SNAPSHOT")
        if not isinstance(events, list):
            events = []
        values = {name: self.safe(local_values[name], "DECLARED_FLOW_LOCAL_" + name)
                  for name in flow["native_local_names"] if name in local_values}
        diagnostic_owners = [event for event in events if event["kind"] in {"OWNER", "REC_CONSUMER", "CURRENTNESS", "COLD_CONSTRUCTOR"}]
        owners = [event for event in diagnostic_owners if _complete(event["args"]) and _complete(event["kwargs"])
            and ("actual_error" in event or _complete(event.get("actual_return")))]
        returned = [event for event in owners if "actual_return" in event or "actual_error" in event]
        cold = [event for event in events if event["kind"] == "COLD_CONSTRUCTOR"]
        current = [event for event in owners if event["kind"] == "CURRENTNESS"]
        native_files = [item for snapshots in self.files.values() for item in snapshots]
        fields = {}
        dispositions = {name: {"status": "UNMEASURED", "reason": "EXACT_NATIVE_BOUNDARY_OR_VALUE_NOT_REACHED"} for name in FIELDS}

        def record(name, value, scope, point=None):
            fields[name] = {"records": [{"status": "MEASURED", "native_value": value,
                "evidence_scope": scope, "native_point": point or original_point,
                "selector": selector, "actual_pid": os.getpid()}]}
            dispositions.pop(name, None)

        if owners:
            record("exact_native_operation_identity", {"actual_owner_inputs": owners},
                "ACTUALLY_REACHED_NATIVE_OWNER_INPUTS_AND_RETURNED_IDENTITIES; NO_ROW_VERDICT")
        if returned or native_files:
            record("native_intent_result_refs", {"actual_owner_values": returned, "actual_single_file_bytes": native_files},
                "ACTUAL_NATIVE_RESULTS_AND_TEST_OWNED_BYTES; NO_SYNTHETIC_CANONICAL_RECORD")
        if current:
            record("native_currentness_revisions", current,
                "EXACT_NATIVE_CURRENTNESS_READS_OR_OWNER_SOURCE_VERSION_REFS_AS_RETURNED")
        # Only the predeclared native JSONL integrity-read refusal is an
        # observed source-currentness outcome. No constructor failure, revision,
        # successful read, empty dispatch count or cold reopen is invented.
        refusal = flow.get("native_integrity_read_refusal")
        if "native_currentness_revisions" not in fields and isinstance(refusal, dict):
            reader = refusal["reader"]
            reader_path = (ROOT / reader["path"]).resolve()
            if (reader_path.is_relative_to(ROOT) and reader_path.is_file()
                    and hashlib.sha256(reader_path.read_bytes()).hexdigest() == reader["sha256"]):
                matched_frame_function = reader["frame_function"]
                if (row == "D2/REC-C"
                        and selector == "backend/tests/test_fpbca_final65_rec_c_p1_security_durability_identity_closure.py::test_native_observed_jsonl_writer_fails_closed_on_duplicate_committed_identity"
                        and original_point["sha256"] == "8cfe0329d7059ff8c5acee17aeb1c760ea2b685ff2e132ed043363c4c2510ba8"
                        and reader["sha256"] == "d6d8423ce733e3c85fdb2ac04b52ef3fbf9b584e27d2bdbe3f820c07b28d8401"):
                    matched_frame_function = "append_record_once"
                reached = [event for event in owners
                    if event["native_point"]["function"] == refusal["owner_symbol"]
                    and "actual_error" in event
                    and any(Path(frame["path"]).resolve() == reader_path
                        and frame["function"] == matched_frame_function
                        for frame in event["actual_error"].get("frames", ()))
                    and any(item.get("exists") is True and isinstance(item.get("hex"), str)
                        for item in event.get("files_before", ()))]
                if reached:
                    record("native_currentness_revisions", {"native_integrity_read_refusals": reached,
                        "actual_reader_source": {**reader, "actual_matched_frame_function": matched_frame_function}, "native_revision": "NOT_PROVIDED_BY_THIS_FILE_OWNER"},
                        "ACTUAL_NATIVE_CAPTURE_FILE_INTEGRITY_READ_REFUSAL_AND_EXACT_BEFORE_BYTES; NO_SUCCESS_CURRENTNESS_OR_LIFECYCLE_REVISION_INFERENCE")
        cuts = [event for event in events if "actual_error" in event and event["kind"] != "EXACT_EIGHT_TRANSPORT_SUPPORT"]
        if cuts:
            record("actual_cut_location", cuts, "ACTUAL_NATIVE_EXCEPTION_FRAMES; AUTHORED_LOCAL_CUT_IS_NOT_OS_CRASH")
        else:
            for name, keys in flow.get("native_cut_local_projection", {}).items():
                source = local_values.get(name)
                if isinstance(source, dict) and all(key in source for key in keys):
                    projected = self.safe({key: source[key] for key in keys}, "ACTUAL_NATIVE_CUT_LOCAL_PROJECTION")
                    if _complete(projected):
                        record("actual_cut_location", {"actual_native_cut_material": projected, "declared_source": original_point},
                            "SOURCE_BOUND_EXISTING_NATIVE_FIXTURE_DURABLE_RESULT_CUT_MATERIAL; NO_OS_CRASH_OR_CLOSURE_INFERENCE")

        if "actual_cut_location" not in fields:
            boundary_symbols = flow.get("native_durable_observation_symbols", ())
            observed_boundaries = [event for event in owners
                if event["native_point"]["function"] in boundary_symbols
                and "actual_return" in event
                and isinstance(event["actual_return"], dict)
                and ("state" in event["actual_return"] or "outcome" in event["actual_return"])]
            if observed_boundaries:
                record("actual_cut_location", {"actual_native_durable_state_observation": observed_boundaries,
                    "os_crash": False, "fault_injection": False},
                    "EXACT_UNCHANGED_NATIVE_RESERVED_DISPATCHED_RESULT_OR_REC_CONSUMER_OBSERVATION_BOUNDARY; NOT_OS_CRASH_OR_WORKER_CLOSURE")

        cold_groups = {}
        publication_groups = {}
        for event in cold:
            symbol = event["native_point"]["function"]
            projection = event.get("constructor_subset_projection")
            if (symbol in flow.get("cold_symbols", ()) and symbol == "PostWriteReleasePublicationService.__init__"
                    and isinstance(projection, dict) and _complete(projection)):
                key = json.dumps(projection["same_storage_and_component_input_key"], sort_keys=True, ensure_ascii=False)
                publication_groups.setdefault(key, []).append({"native_point": event["native_point"],
                    "native_constructor_sequence": event["sequence"], "native_pid": event["pid"],
                    "native_constructor_subset": projection})
            elif (symbol in flow.get("cold_symbols", ())
                    and _complete(event["args"]) and _complete(event["kwargs"])):
                key = (symbol, json.dumps({"args": event["args"], "kwargs": event["kwargs"]}, sort_keys=True, ensure_ascii=False))
                cold_groups.setdefault(key, []).append(event)
        reopened = [group for group in cold_groups.values() if len({event["native_instance_id"] for event in group}) >= 2]
        projected_reopened = [group for group in publication_groups.values()
            if len({event["native_constructor_subset"]["native_owner_identity"]["object_id"] for event in group}) >= 2
            and len({event["native_constructor_subset"]["native_store_identity"]["object_id"] for event in group}) >= 2]
        if flow.get("cold_reopen") and (reopened or projected_reopened):
            record("native_cold_reopen_outcome", {"same_existing_owner_constructor_and_input": reopened,
                "same_publication_storage_and_component_subset_distinct_native_owners": projected_reopened,
                "native_values": returned},
                "ACTUAL_DISTINCT_EXISTING_OWNER_CONSTRUCTORS_AND_NATIVE_RETURN_VALUES; PUBLICATION_SUBSET_IS_NOT_FULL_KWARGS_EQUALITY; NOT_PROCESS_RESTART")
        # Only the eight reviewed binding scopes consume supporting frames.
        # These frames are excluded from generic owner/currentness/count fields.
        support = [event for event in events if event["kind"] == "EXACT_EIGHT_TRANSPORT_SUPPORT"]
        c_full = "backend/tests/test_fpbca_final65_rec_c_full_chain_eval_capture.py::"
        c_p1 = "backend/tests/test_fpbca_final65_rec_c_p1_security_durability_identity_closure.py::"
        d_direct = "backend/tests/test_fpbca_final65_rec_d_multi_agent_v3_memory_runtime.py::"
        if (row == "D2/REC-C" and original_point["sha256"] == "d7ce4772d1ed2ea51ec90d98c38e4657d70450dda31bf0c47247fd220310586a"
                and selector == c_full + "test_native_observed_T02_raw_prefix_survives_downstream_business_commit_failure"):
            received = [event for event in support if event["native_point"]["function"] == "DiagnosticSession.__exit__"
                and "actual_received_exception" in event and "actual_return" in event]
            if received:
                record("actual_cut_location", {"actual_context_exit_received_exception": received},
                    "ACTUAL_EXISTING_DOWNSTREAM_EXCEPTION_ARGUMENT_AND_SINGLE_RETURNING_CONTEXT_EXIT; NOT_EXIT_FAILURE_OR_OS_CRASH")
        if (row == "D2/REC-C" and original_point["sha256"] == "d7ce4772d1ed2ea51ec90d98c38e4657d70450dda31bf0c47247fd220310586a"
                and selector == c_full + "test_native_observed_T11_capture_failure_keeps_business_output_and_exposes_incomplete"):
            prefix = local_values.get("prefix")
            keys = ("completeness", "limitations", "seal", "source_sha256")
            reached = [event for event in current if event["native_point"]["function"] == "read_diagnostic_prefix" and "actual_return" in event]
            if isinstance(prefix, dict) and all(key in prefix for key in keys) and reached:
                projected = self.safe({key: prefix[key] for key in keys}, "EXACT_NATIVE_DIAGNOSTIC_PREFIX_CUT")
                if _complete(projected) and any(all(event["actual_return"].get(key) == projected[key] for key in keys) for event in reached):
                    record("actual_cut_location", {"actual_native_prefix_cut": projected, "native_read_events": reached},
                        "ACTUAL_RETURNED_DIAGNOSTIC_PREFIX_INCOMPLETENESS_SEAL_AND_LIMITATIONS; NO_PARAMETER_LABEL_OR_OS_CRASH_INFERENCE")
        if (row == "D2/REC-C" and original_point["sha256"] == "8cfe0329d7059ff8c5acee17aeb1c760ea2b685ff2e132ed043363c4c2510ba8"
                and selector == c_p1 + "test_native_observed_jsonl_writer_recovers_torn_tail_before_idempotent_append"):
            reads = [event for event in support if event["native_point"]["function"] == "CrashSafeJsonlRecordSink._read_records_unlocked"
                and "actual_return" in event and _complete(event["actual_return"])
                and any(item.get("exists") is True and isinstance(item.get("hex"), str) for item in event.get("files_before", ()))]
            if reads:
                record("native_currentness_revisions", {"actual_capture_prefix_recovery_read": reads, "native_revision": "NOT_PROVIDED_BY_THIS_FILE_OWNER"},
                    "ACTUAL_NATIVE_SINGLE_READ_RETURN_AND_BEFORE_AFTER_CAPTURE_BYTES; NO_LIFECYCLE_REVISION_OR_COLD_REOPEN_INFERENCE")
        direct_scope = (row == "D2/REC-D" and original_point["sha256"] == "dc971c616400a992d23fdd53f132edeba307be3df5d7883e4c6375d01661ab97")
        if direct_scope and selector in {d_direct + "test_native_observed_index_failure_after_memory_commit_does_not_roll_back_memory",
                d_direct + "test_native_observed_postgres_transaction_commit_failure_rolls_back_canonical_record"}:
            source_reads = [event for event in support if event["native_point"]["function"] == "DurableMemoryRuntime._resolve_projection_sources"
                and "actual_return" in event and _complete(event["args"]) and _complete(event["kwargs"]) and _complete(event["actual_return"])]
            if source_reads:
                record("native_currentness_revisions", {"actual_projection_source_binding_reads": source_reads},
                    "ACTUAL_EXISTING_TYPED_NATIVE_SOURCE_BINDING_AND_DOMAIN_RESOLUTION_RETURN; CALLBACK_IDENTITY_IS_NOT_PERMISSION; NO_REAL_POSTGRES")
        if direct_scope and selector == d_direct + "test_native_observed_index_failure_after_memory_commit_does_not_roll_back_memory":
            project_errors = [event for event in support if event["native_point"]["function"] == "DurableMemoryRuntime.project_source" and "actual_error" in event]
            if project_errors:
                record("actual_cut_location", {"actual_existing_memory_projection_errors": project_errors},
                    "ACTUAL_SINGLE_NATIVE_PROJECTION_CALL_ERROR_RERAISED_TO_ORIGINAL_CONTROL; NO_NEW_OPERATION_OR_OS_CRASH")
        if (row == "D2/REC-D" and original_point["sha256"] == "7507f2af7b3f3e980695ae78895a9e9bf1409e14984c6588767679b3ee52b88f"
                and selector == "backend/tests/test_fpbca_final65_rec_d_step7_durable_memory_persistence.py::test_native_observed_crash_after_source_before_memory_recovers_from_exact_source"):
            memory_groups = {}
            for event in cold:
                projection = event.get("memory_constructor_subset_projection")
                if (event["native_point"]["function"] == "TargetDurableMemoryComposition.__init__"
                        and "actual_return" in event and isinstance(projection, dict) and _complete(projection)):
                    key = json.dumps(projection["same_storage_and_component_input_key"], sort_keys=True, ensure_ascii=False)
                    memory_groups.setdefault(key, []).append({"native_point": event["native_point"], "native_constructor_sequence": event["sequence"],
                        "native_constructor_subset": projection})
            memory_reopened = [group for group in memory_groups.values()
                if len({event["native_constructor_subset"]["native_owner_identity"]["object_id"] for event in group}) >= 2
                and len({event["native_constructor_subset"]["native_runtime_identity"]["object_id"] for event in group}) >= 2]
            if memory_reopened:
                record("native_cold_reopen_outcome", {"actual_distinct_memory_constructor_same_storage_source_component_subset": memory_reopened,
                    "native_values": returned},
                    "ACTUAL_SUCCESSFUL_DISTINCT_SAME_PROCESS_MEMORY_OWNER_AND_RUNTIME_WITH_SAME_NATIVE_STORE_SOURCE_COMPONENTS; FULL_KWARGS_UNKNOWN; NOT_OS_RESTART_OR_NEW_READ_PERMISSION")


        replay_calls = [event for event in events if event["kind"] != "EXACT_EIGHT_TRANSPORT_SUPPORT"
            and event["native_point"]["function"] in flow.get("replay_symbols", ())]
        grouped = {}
        for event in replay_calls:
            key = json.dumps({"symbol": event["native_point"]["function"], "args": event["args"], "kwargs": event["kwargs"]}, sort_keys=True, ensure_ascii=False)
            grouped.setdefault(key, []).append(event)
        repeats = [group for group in grouped.values() if len(group) >= 2]
        semantic_replays = []
        semantic = flow.get("native_capture_semantic_replay")
        if isinstance(semantic, dict):
            stable_groups = {}
            for event in owners:
                if event["native_point"]["function"] != "JsonlWriter.write_once" or "actual_return" not in event:
                    continue
                args, kwargs = event["args"], event["kwargs"]
                if not (len(args) == 1 and isinstance(args[0], dict)
                        and kwargs.get("id_field") == "capture_id"
                        and isinstance(args[0].get("capture_id"), str)
                        and isinstance(kwargs.get("semantic_fingerprint"), str)
                        and kwargs.get("semantic_projection", {}).get("callback_source_identity_only") == semantic["projection_point"]
                        and isinstance(event["actual_return"], str)):
                    continue
                key = (args[0]["capture_id"], kwargs["semantic_fingerprint"], event["actual_return"])
                stable_groups.setdefault(key, []).append(event)
            semantic_replays = [group for group in stable_groups.values()
                if len({event["sequence"] for event in group}) >= 2]
            if semantic_replays:
                record("exact_replay_outcome", {"actual_same_capture_id_semantic_fingerprint_path_calls": semantic_replays,
                    "native_values_and_files": native_files,
                    "different_attempt_envelope": "RETAINED_EXACTLY_IN_ACTUAL_NATIVE_ARGS",
                    "projection_source_identity": semantic["projection_point"]},
                    "ACTUALLY_REPEATED_NATIVE_CAPTURE_SEMANTIC_ID_AND_FINGERPRINT; NATIVE_OWNER_VALIDATES_EXISTING_PROJECTION; NOT_WHOLE_TELEMETRY_ARGUMENT_EQUALITY")
        if flow.get("exact_replay") and repeats:
            record("exact_replay_outcome", {"actual_same_input_owner_calls": repeats, "actual_native_files": native_files},
                "ACTUALLY_REPEATED_EXACT_OWNER_INPUTS_AND_RESULTS; ORACLES_REMAIN_IN_UNCHANGED_CONTROL")
        if flow.get("exact_replay") and "exact_replay_outcome" not in fields:
            publication_replay = self._publication_replay_observation(
                row=row, events=events, flow=flow, original_point=original_point)
            if publication_replay is not None:
                record("exact_replay_outcome", publication_replay, publication_replay["scope"])
        if flow.get("concurrent"):
            threads = {event["thread_id"] for event in owners}
            if len(threads) >= 2:
                record("concurrent_duplicate_outcome", {"actual_native_threads": sorted(threads), "native_owner_values": returned},
                    "ACTUALLY_ENTERED_NATIVE_OWNER_THREADS_AND_RETURN_VALUES; NO_AT_MOST_ONCE_POLICY_INFERENCE")

        if "exact_replay_outcome" not in fields:
            tool_receipt = self._tool_passive_receipt_observation(
                row=row, flow=flow, original_point=original_point)
            if tool_receipt is not None:
                record("exact_replay_outcome", tool_receipt,
                    "EXACT_EXISTING_NATIVE_TOOL_CONTROL_PASSIVE_CUSTODY_OR_ACTIVE_ALIAS_OUTCOME; NEGATIVE_OR_INDETERMINATE_RETAINS_NO_RETRY_PERMISSION; NOT_IDENTICAL_TOOL_INVOKE_ARGUMENT_OR_CLASS_WIDE_CRASH_PASS")

        dispatch = [event for event in events if event["kind"] == "ACTUAL_DISPATCH"]
        suffix = flow.get("dispatch_root_suffix")
        if suffix:
            dispatch = [event for event in dispatch if str(event.get("instance_before", {}).get("root", "")).replace("\\", "/").endswith(suffix)]
        baseline_dispatch = [event for event in dispatch if self.flow_start_sequence is not None and event["sequence"] < self.flow_start_sequence]
        flow_dispatch = [event for event in dispatch if self.flow_start_sequence is not None and event["sequence"] >= self.flow_start_sequence]
        payload_counter_scope = {"before_flow_actual_entries": baseline_dispatch, "during_flow_actual_entries": flow_dispatch,
            "scope": flow["dispatch_scope"], "later_fixture_teardown": "OUTSIDE_THIS_FLOW_WINDOW_UNMEASURED"}
        if flow.get("explicit_handler_counter"):
            name, attr = flow["explicit_handler_counter"]
            instance = local_values.get(name)
            counter = vars(instance).get(attr) if instance is not None and hasattr(instance, "__dict__") else None
            if isinstance(counter, list):
                # This is the existing initialized and append-at-actual-handler
                # entry counter; neither a missing dict key nor hook absence.
                record("actual_physical_dispatch_count", len(counter),
                    flow["dispatch_scope"] + "; EXACT_EXISTING_INITIALIZED_HANDLER_REQUEST_LIST",
                    dispatch[0]["native_point"] if dispatch else original_point)
                payload_counter_scope["actual_existing_handler_request_list"] = self.safe(counter, "EXACT_HANDLER_REQUEST_COUNTER")
                payload_counter_scope["native_port_entry_is"] = "SUPPORTING_ONLY; BACKEND_BEFORE_CUT_MAY_ENTER_PORT_WITH_NO_AUTHORED_BACKEND_DISPATCH"
        elif dispatch:
            record("actual_physical_dispatch_count", len(flow_dispatch), flow["dispatch_scope"], dispatch[0]["native_point"])
        states = [event for event in events if event["kind"] == "BUDGET_APPEND" and "actual_return" in event]
        transitions, previous = [], {}
        for event in states:
            state = event.get("kwargs", {}).get("state")
            if not isinstance(state, dict):
                continue
            owner_key = (state.get("task_id"), state.get("run_id"))
            rows = state.get("reservations")
            if not isinstance(rows, list):
                continue
            prior = previous.setdefault(owner_key, {})
            for reservation in rows:
                ref = reservation.get("reservation_ref", {})
                key = ref.get("reservation_id")
                old = prior.get(key)
                if (old is not None and old.get("status") == "active"
                        and reservation.get("status") in {"consumed", "released"}
                        and ref.get("reservation_kind") in flow.get("terminal_reservation_kinds", ["model_provider_attempt_tokens"])
                        and isinstance(reservation.get("terminal_budget_revision"), int)
                        and reservation["terminal_budget_revision"] == state.get("revision")):
                    transitions.append({"before": old, "after": reservation,
                        "native_budget_revision": state.get("revision"), "native_event": event})
                prior[key] = reservation
        baseline_transitions = [r for r in transitions if self.flow_start_sequence is not None and r["native_event"]["sequence"] < self.flow_start_sequence]
        flow_transitions = [r for r in transitions if self.flow_start_sequence is not None and r["native_event"]["sequence"] >= self.flow_start_sequence]
        if states:
            record("actual_settlement_count", len(flow_transitions),
                flow.get("settlement_scope", "ACTUAL_CANONICAL_PROVIDER_TOKEN_ACTIVE_TO_CONSUMED_OR_RELEASED_TRANSITIONS; NO_BALANCE_OR_DB_COMMIT_SUBSTITUTION"),
                states[0]["native_point"])
        # Scalar zeros above require actual native Budget append frames in a
        # fresh fully observed journal scope. Registered hooks alone never emit 0.
        for field, binding in flow.get("field_dispositions", {}).items():
            if field not in fields:
                dispositions[field] = binding

        payload = {"schema_version": "fpbca_explicit_native_field_observation_v1", "row": row,
            "selector": selector, "observations": fields, "field_dispositions": dispositions,
            "actual_native_owner_events": events, "explicit_flow_values": values,
            "actual_registered_dispatch_scope": payload_counter_scope,
            "actual_settlement_scope": {"before_flow": baseline_transitions, "during_flow": flow_transitions, "later_fixture_teardown": "OUTSIDE_THIS_FLOW_WINDOW_UNMEASURED"},
            "observer_gaps": self.gaps, "registered_hooks": self.installations,
            "scope": flow["scope"], "not_claimed": ["GLOBAL_VERDICT", "BUSINESS_SUCCESS", "REAL_PROVIDER",
                "LIVE_POSTGRES", "REAL_PUBLICATION", "OS_CRASH_FROM_CONTROLLED_EXCEPTION", "CROSS_OWNER_ATOMICITY"]}
        durable_gaps = [gap for gap in self.gaps if gap.get("phase") == "DURABLE_DIAGNOSTIC_EVENT"]
        if durable_gaps:
            # A missing durable event can invalidate the closed counter scope.
            # Serializer gaps in unrelated supporting constructor arguments are
            # retained; the exact unresolved owner events were excluded above.
            payload["observations"] = {}
            payload["field_dispositions"] = {name: {"status": "UNMEASURED", "reason": "ACTUAL_DURABLE_OBSERVER_COVERAGE_GAP"} for name in FIELDS}
        _once(self.root / "native-row-observations" / row.replace("/", "_") / "observation.json", payload)
