"""Explicit execution boundary. Observe never imports execution owners."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping


class AuthorizationError(ValueError):
    pass


def authorize_mode(mode: str, authorization: Mapping[str, Any] | None = None,
                   *, now: datetime | None = None) -> str:
    mode = mode.replace("-", "_").upper()
    if mode in ("OBSERVE", "ISOLATED_REPLAY"):
        return mode
    if mode != "FRESH_BENCHMARK":
        raise AuthorizationError("UNKNOWN_EXECUTION_MODE")
    auth = authorization or {}
    required = ("authorization_id", "authorized_by", "models", "input_roots", "operations", "budget", "expires_at")
    if auth.get("schema_version") != "full_chain_run_authorization_v1" or any(not auth.get(k) for k in required):
        raise AuthorizationError("PENDING_RUN_AUTHORIZATION")
    expiry = datetime.fromisoformat(str(auth["expires_at"]).replace("Z", "+00:00"))
    if expiry.tzinfo is None or expiry <= (now or datetime.now(timezone.utc)):
        raise AuthorizationError("AUTHORIZATION_EXPIRED")
    if any(x in auth["operations"] for x in ("publish", "recover", "activate", "reconcile")):
        raise AuthorizationError("OPERATION_OUTSIDE_EVALUATION_SCOPE")
    return mode


def invoke_scenario(mode: str, scenario: str, factories: Mapping[str, Callable],
                    inputs: Mapping[str, Any], *, authorization=None,
                    exact_request_hash=None, recorded_request_hash=None,
                    injection=False):
    mode = authorize_mode(mode, authorization)
    if mode == "OBSERVE":
        raise AuthorizationError("OBSERVE_EXECUTION_FORBIDDEN")
    if mode == "FRESH_BENCHMARK":
        # Only existing trusted runtime factories may be used, with the concrete
        # request resources bound to the authorization, never a manifest import.
        model = inputs.get("model")
        if model not in authorization["models"] or inputs.get("operation") not in authorization["operations"]:
            raise AuthorizationError("REQUEST_OUTSIDE_AUTHORIZATION")
        path = Path(inputs.get("input_root", "")).resolve(strict=True)
        if not any(path == Path(r).resolve(strict=True) or path.is_relative_to(Path(r).resolve(strict=True))
                   for r in authorization["input_roots"]):
            raise AuthorizationError("INPUT_OUTSIDE_AUTHORIZATION")
    elif inputs.get("provider") not in (None, "fake", "fixed"):
        raise AuthorizationError("ISOLATED_LIVE_PROVIDER_FORBIDDEN")
    if exact_request_hash != recorded_request_hash and not injection:
        raise AuthorizationError("EXACT_REPLAY_REQUEST_MISMATCH")
    if scenario not in factories or not isinstance(scenario, str):
        raise AuthorizationError("UNTRUSTED_SCENARIO")
    if any(k in inputs for k in ("gold", "labels", "reviewer_notes", "expected_verdict")):
        raise AuthorizationError("REFERENCE_LEAKAGE_REJECTED")
    kind='FRESH' if mode=='FRESH_BENCHMARK' else 'INJECTION' if injection else 'EXACT' if exact_request_hash is not None else 'FIXED_SCENARIO'
    return {"replay_kind":kind,"output":factories[scenario](dict(inputs))}


class ReferenceSeparatedRuntime:
    """Existing ExperimentRuntime port, with labels excluded from runtime input."""
    def __init__(self, delegate):
        self.delegate=delegate

    def run(self,sample,experiment):
        # StrategyEvalRunner keeps the independent original sample for metrics;
        # only this label-free copy crosses into the tested production runtime.
        clean=sample.model_copy(update={name: [] for name in (
            'expected_doc_ids','expected_parent_chunk_ids','expected_child_chunk_ids',
            'expected_keywords','answer_keywords','keyword_doc_ids','source_refs')})
        clean=clean.model_copy(update={'reference_answer':'','metadata':{},'golden_set_id':None,'golden_set_version':None})
        return self.delegate.run(clean,experiment)

    def metadata(self): return self.delegate.metadata()
    def close(self): return self.delegate.close()


def guarded_runtime_factory(mode, trusted_factory, *, resources, authorization=None):
    """Supply this factory to existing StrategyEvalRunner; no second scheduler."""
    authorize_mode(mode,authorization)
    class GuardedFactory:
        def build(self,**kwargs):
            factory=trusted_factory.build if hasattr(trusted_factory,'build') else trusted_factory
            # Existing StrategyEvalRunner calls build; an ordinary callback alone
            # cannot satisfy that port. Validate before invoking either form.
            output=invoke_scenario(mode,'existing-runtime',
                {'existing-runtime':lambda _inputs:factory(**kwargs)},resources,
                authorization=authorization)
            return ReferenceSeparatedRuntime(output['output'])
    return GuardedFactory()


def fixed_gateway_replay(request_material, *, capture_root, allowed_root):
    """One declared physical call through real Gateway, with a fixed client."""
    from contracts.base_client import BaseLLMClient
    from core.diagnostics import DiagnosticConfig,DiagnosticSession
    from model_gateway.model_gateway import ModelGateway
    from schemas.model import ModelRequestSchema,ModelResponseSchema
    class FixedClient(BaseLLMClient):
        model_name=request_material.get('request',{}).get('model_name') or 'full-chain-fixed-replay'
        physical_calls=0
        def generate(self,request):
            self.physical_calls+=1
            self.physical_request=request
            return ModelResponseSchema(model_call_id=request.model_call_id,task_id=request.task_id,
                run_id=request.run_id,model_name=self.model_name,content=request_material['fixed_response'],
                success=True,finish_reason='stop',created_at=request.created_at)
    if set(request_material)-{'request','fixed_response','request_hash','recorded_request_hash','injection'}:
        raise AuthorizationError('UNDECLARED_REPLAY_FIELDS')
    from .contracts import canonical_hash
    actual=request_material['request']
    if canonical_hash(actual)!=request_material.get('request_hash'):
        raise AuthorizationError('REPLAY_REQUEST_HASH_MISMATCH')
    if request_material.get('request_hash')!=request_material.get('recorded_request_hash') and not request_material.get('injection'):
        raise AuthorizationError('EXACT_REPLAY_REQUEST_MISMATCH')
    request=ModelRequestSchema.model_validate(actual)
    client=FixedClient()
    gateway=ModelGateway(FixedClient.model_name); gateway.register_client(client)
    diagnostic=DiagnosticSession(DiagnosticConfig(capture_root,allowed_root,'fixed-replay'))
    with diagnostic:
        result=gateway.generate(request)
    return {'response':result.model_dump(mode='json'),'capture_path':str(diagnostic.path),
        'replay_kind':'INJECTION' if request_material.get('injection') else 'EXACT',
        'real_provider_calls':0,'fixed_physical_calls':client.physical_calls}
