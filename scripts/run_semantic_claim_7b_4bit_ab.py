"""Offline 3B-vs-7B Semantic ClaimType A/B diagnostic.

This tool does not rerun retrieval, Writer, Gate, or the production Model Gateway.
It reads the persisted 3B classifications from one E2E raw-interaction capture,
then classifies the exact same sentences with a local Qwen2.5-7B-Instruct model
loaded through bitsandbytes 4-bit quantization.  The production semantic prompt
builder/parser are imported directly so prompt semantics stay frozen.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from apps.enterprise_document.services.scheme_writer.semantic_claim_classifier import (  # noqa: E402
    VALID_CLAIM_TYPES,
    _build_prompt,
    _extract_json_object,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_capture(path: Path) -> dict[str, Any]:
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(rows) != 1:
        raise ValueError(f"expected exactly one raw-interaction row, got {len(rows)}")
    record = rows[0]
    sections = list(dict(record.get("eval_sample") or {}).get("sections") or [])
    if not sections:
        raise ValueError("raw interaction does not contain eval_sample.sections")
    return record


def _load_7b_4bit(model_path: Path):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required for this 7B 4-bit diagnostic")
    if not model_path.exists():
        raise FileNotFoundError(model_path)

    quantization_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.float16,
        bnb_4bit_use_double_quant=True,
    )
    tokenizer = AutoTokenizer.from_pretrained(
        str(model_path),
        trust_remote_code=True,
        local_files_only=True,
    )

    # transformers 4.44.2 assumes newer bitsandbytes models can accept a final
    # model.to(device) when the whole device_map resolves to one CUDA device.
    # bitsandbytes 0.50.0 on Windows still rejects that operation for 4-bit
    # models.  Force Accelerate hooks only for this offline diagnostic so the
    # already-quantized modules stay on CUDA without changing project runtime.
    import transformers.modeling_utils as modeling_utils
    from accelerate.big_modeling import dispatch_model as accelerate_dispatch_model

    original_dispatch_model = modeling_utils.dispatch_model

    def dispatch_model_force_hooks(model_to_dispatch, *args, **kwargs):
        kwargs["force_hooks"] = True
        return accelerate_dispatch_model(model_to_dispatch, *args, **kwargs)

    modeling_utils.dispatch_model = dispatch_model_force_hooks
    try:
        model = AutoModelForCausalLM.from_pretrained(
            str(model_path),
            quantization_config=quantization_config,
            device_map={"": 0},
            trust_remote_code=True,
            local_files_only=True,
        )
    finally:
        modeling_utils.dispatch_model = original_dispatch_model
    model.eval()
    return tokenizer, model


def _format_prompt(tokenizer: Any, *, system_prompt: str, prompt: str) -> str:
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]
    return tokenizer.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=True,
    )


def _classify_batch(
    *,
    tokenizer: Any,
    model: Any,
    section_title: str,
    batch: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    import torch

    system_prompt, prompt = _build_prompt(
        section_title=section_title,
        sentences=batch,
    )
    prompt_text = _format_prompt(
        tokenizer,
        system_prompt=system_prompt,
        prompt=prompt,
    )
    inputs = tokenizer(prompt_text, return_tensors="pt")
    model_device = next(model.parameters()).device
    moved = {key: value.to(model_device) for key, value in inputs.items()}
    max_new_tokens = max(192, min(640, len(batch) * 72))

    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()
    started = time.perf_counter()
    with torch.no_grad():
        output_ids = model.generate(
            **moved,
            max_new_tokens=max_new_tokens,
            do_sample=False,
            pad_token_id=tokenizer.eos_token_id,
        )
    latency_ms = int((time.perf_counter() - started) * 1000)
    generated_ids = output_ids[0][moved["input_ids"].shape[-1] :]
    content = tokenizer.decode(generated_ids, skip_special_tokens=True).strip()

    parsed: dict[str, dict[str, Any]] = {}
    parse_error = None
    try:
        payload = _extract_json_object(content)
        items = payload.get("items")
        if not isinstance(items, list):
            raise ValueError("items must be a list")
        expected = {str(item["id"]) for item in batch}
        for item in items:
            if not isinstance(item, dict):
                continue
            sentence_id = str(item.get("id") or "").strip()
            if sentence_id not in expected or sentence_id in parsed:
                continue
            raw_types = item.get("claim_types")
            if not isinstance(raw_types, list):
                continue
            claim_types = [str(value).strip() for value in raw_types]
            if any(value not in VALID_CLAIM_TYPES for value in claim_types):
                continue
            raw_confidence = item.get("confidence")
            contract_errors: list[str] = []
            confidence: float | None = None
            if raw_confidence is None:
                contract_errors.append("missing_confidence")
            else:
                try:
                    confidence = float(raw_confidence)
                except (TypeError, ValueError):
                    contract_errors.append("invalid_confidence")
                else:
                    if not 0.0 <= confidence <= 1.0:
                        contract_errors.append("confidence_out_of_range")
                        confidence = None
            parsed[sentence_id] = {
                "claim_types": list(dict.fromkeys(claim_types)),
                "confidence": confidence,
                "contract_valid": not contract_errors,
                "contract_errors": contract_errors,
            }
    except Exception as exc:
        parse_error = f"{type(exc).__name__}: {exc}"

    call = {
        "batch_sentence_ids": [str(item["id"]) for item in batch],
        "latency_ms": latency_ms,
        "prompt_tokens": int(moved["input_ids"].shape[-1]),
        "completion_tokens": int(generated_ids.shape[-1]),
        "max_new_tokens": max_new_tokens,
        "parse_error": parse_error,
        "response_preview": content[:1000] if parse_error else None,
        "peak_cuda_memory_bytes": (
            int(torch.cuda.max_memory_allocated()) if torch.cuda.is_available() else None
        ),
    }
    return parsed, call


def _classify_with_retries(
    *,
    tokenizer: Any,
    model: Any,
    section_title: str,
    rows: list[dict[str, Any]],
    batch_size: int,
    max_retry_depth: int = 3,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    classifications: dict[str, dict[str, Any]] = {}
    calls: list[dict[str, Any]] = []

    def run_batch(batch: list[dict[str, Any]], retry_depth: int, label: str) -> None:
        if not batch:
            return
        parsed, call = _classify_batch(
            tokenizer=tokenizer,
            model=model,
            section_title=section_title,
            batch=batch,
        )
        classifications.update(parsed)
        expected = {str(item["id"]) for item in batch}
        missing = expected - set(parsed)
        call["batch_label"] = label
        call["retry_depth"] = retry_depth
        call["parsed_sentence_ids"] = sorted(parsed)
        call["missing_sentence_ids"] = sorted(missing)
        calls.append(call)

        if missing and retry_depth < max_retry_depth:
            retry_rows = [item for item in batch if str(item["id"]) in missing]
            if len(retry_rows) == 1:
                run_batch(retry_rows, retry_depth + 1, f"{label}.retry")
            else:
                midpoint = max(1, len(retry_rows) // 2)
                run_batch(retry_rows[:midpoint], retry_depth + 1, f"{label}.a")
                run_batch(retry_rows[midpoint:], retry_depth + 1, f"{label}.b")

    for start in range(0, len(rows), batch_size):
        run_batch(
            rows[start : start + batch_size],
            0,
            f"batch_{start // batch_size + 1}",
        )
    return classifications, calls


def _gate_violations(
    claim_types: list[str],
    *,
    allowed: set[str],
    forbidden: set[str],
) -> list[dict[str, str]]:
    violations: list[dict[str, str]] = []
    for claim_type in claim_types:
        if claim_type in forbidden:
            violations.append(
                {
                    "violation_code": "forbidden_claim_type_generated",
                    "claim_type": claim_type,
                }
            )
        elif claim_type not in allowed:
            violations.append(
                {
                    "violation_code": "claim_type_not_allowed",
                    "claim_type": claim_type,
                }
            )
    return violations


def _prepare_section(section: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    audit = dict(dict(section.get("extra") or {}).get("claim_audit") or {})
    claims = list(audit.get("generated_claims") or [])
    rows: list[dict[str, Any]] = []
    for claim in sorted(claims, key=lambda item: int(item.get("sentence_index") or 0)):
        sentence_index = int(claim.get("sentence_index") or 0)
        rows.append(
            {
                "id": f"s{sentence_index}",
                "text": str(claim.get("text") or ""),
                "sentence_index": sentence_index,
                "original_3b": {
                    "claim_types": [str(claim.get("claim_type") or "")],
                    "confidence": float(claim.get("confidence") or 0.0),
                    "model_name": str(
                        dict(claim.get("metadata") or {}).get("semantic_model_name") or ""
                    ),
                    "model_call_id": str(
                        dict(claim.get("metadata") or {}).get("semantic_model_call_id") or ""
                    ),
                },
            }
        )
    return rows, audit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-jsonl", type=Path, required=True)
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--section-order", type=int, default=None)
    args = parser.parse_args()
    if args.batch_size <= 0:
        raise ValueError("--batch-size must be positive")

    record = _load_capture(args.source_jsonl)
    sections = list(dict(record["eval_sample"]).get("sections") or [])
    if args.section_order is not None:
        sections = [
            section
            for section in sections
            if int(section.get("section_order") or 0) == args.section_order
        ]
        if not sections:
            raise ValueError(f"section_order={args.section_order} not found")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    progress_path = args.output.with_name(f"{args.output.stem}.progress.json")

    print(f"[7B4bitAB] loading model: {args.model_path}", flush=True)
    load_started = time.perf_counter()
    tokenizer, model = _load_7b_4bit(args.model_path)
    load_ms = int((time.perf_counter() - load_started) * 1000)
    print(
        f"[7B4bitAB] loaded in {load_ms}ms device_map={getattr(model, 'hf_device_map', None)}",
        flush=True,
    )

    all_rows: list[dict[str, Any]] = []
    all_calls: list[dict[str, Any]] = []
    section_reports: list[dict[str, Any]] = []
    old_type_counts: Counter[str] = Counter()
    new_type_counts: Counter[str] = Counter()
    old_violation_codes: Counter[str] = Counter()
    new_violation_codes: Counter[str] = Counter()

    for position, section in enumerate(sections, start=1):
        title = str(section.get("section_title") or "")
        order = int(section.get("section_order") or 0)
        rows, audit = _prepare_section(section)
        allowed = set(audit.get("allowed_claim_types") or [])
        forbidden = set(audit.get("forbidden_claim_types") or [])
        print(
            f"[7B4bitAB] section={position}/{len(sections)} order={order} title={title} sentences={len(rows)}",
            flush=True,
        )
        new_classifications, calls = _classify_with_retries(
            tokenizer=tokenizer,
            model=model,
            section_title=title,
            rows=rows,
            batch_size=args.batch_size,
        )
        all_calls.extend(
            [{"section_order": order, "section_title": title, **call} for call in calls]
        )

        changed = 0
        missing = 0
        old_section_violations = 0
        new_section_violations = 0
        section_rows: list[dict[str, Any]] = []
        for row in rows:
            old_types = list(row["original_3b"]["claim_types"])
            new_item = new_classifications.get(str(row["id"]))
            new_types = list(new_item["claim_types"]) if new_item else []
            old_gate = _gate_violations(old_types, allowed=allowed, forbidden=forbidden)
            new_gate = _gate_violations(new_types, allowed=allowed, forbidden=forbidden)
            old_type_counts.update(old_types)
            new_type_counts.update(new_types)
            old_violation_codes.update(item["violation_code"] for item in old_gate)
            new_violation_codes.update(item["violation_code"] for item in new_gate)
            old_section_violations += len(old_gate)
            new_section_violations += len(new_gate)
            changed_here = set(old_types) != set(new_types)
            changed += int(changed_here)
            missing += int(new_item is None)
            result_row = {
                "section_order": order,
                "section_title": title,
                "sentence_id": row["id"],
                "sentence_index": row["sentence_index"],
                "text": row["text"],
                "original_3b": row["original_3b"],
                "qwen2_5_7b_4bit": new_item,
                "changed": changed_here,
                "original_3b_gate_violations": old_gate,
                "qwen2_5_7b_4bit_gate_violations": new_gate,
                "gate_impact_changed": old_gate != new_gate,
            }
            section_rows.append(result_row)
            all_rows.append(result_row)

        section_reports.append(
            {
                "section_order": order,
                "section_title": title,
                "sentence_count": len(rows),
                "changed_classification_count": changed,
                "missing_7b_classification_count": missing,
                "original_3b_gate_violation_count": old_section_violations,
                "qwen2_5_7b_4bit_gate_violation_count": new_section_violations,
                "rows": section_rows,
            }
        )
        progress_path.write_text(
            json.dumps(
                {
                    "updated_at": _now_iso(),
                    "completed_sections": len(section_reports),
                    "total_sections": len(sections),
                    "last_section_order": order,
                    "last_section_title": title,
                    "completed_sentence_count": len(all_rows),
                    "section_summaries": [
                        {key: value for key, value in item.items() if key != "rows"}
                        for item in section_reports
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    report = {
        "schema_version": "semantic_claim_3b_vs_7b_4bit_ab_v1",
        "created_at": _now_iso(),
        "source_jsonl": str(args.source_jsonl.resolve()),
        "source_run_id": str(record.get("run_id") or ""),
        "model": {
            "path": str(args.model_path.resolve()),
            "name": "Qwen2.5-7B-Instruct",
            "load_in_4bit": True,
            "quant_type": "nf4",
            "double_quant": True,
            "compute_dtype": "float16",
            "load_latency_ms": load_ms,
            "device_map": getattr(model, "hf_device_map", None),
        },
        "experiment": {
            "same_persisted_sentences": True,
            "original_3b_not_rerun": True,
            "production_prompt_builder_reused": True,
            "retrieval_rerun": False,
            "writer_rerun": False,
            "gate_policy_changed": False,
            "production_model_gateway_changed": False,
            "batch_size": args.batch_size,
        },
        "summary": {
            "section_count": len(section_reports),
            "sentence_count": len(all_rows),
            "changed_classification_count": sum(int(row["changed"]) for row in all_rows),
            "gate_impact_changed_count": sum(
                int(row["gate_impact_changed"]) for row in all_rows
            ),
            "missing_7b_classification_count": sum(
                int(row["qwen2_5_7b_4bit"] is None) for row in all_rows
            ),
            "contract_invalid_7b_classification_count": sum(
                int(
                    row["qwen2_5_7b_4bit"] is not None
                    and not bool(row["qwen2_5_7b_4bit"].get("contract_valid"))
                )
                for row in all_rows
            ),
            "original_3b_claim_type_counts": dict(sorted(old_type_counts.items())),
            "qwen2_5_7b_4bit_claim_type_counts": dict(sorted(new_type_counts.items())),
            "original_3b_gate_violation_count": sum(old_violation_codes.values()),
            "qwen2_5_7b_4bit_gate_violation_count": sum(new_violation_codes.values()),
            "original_3b_gate_violation_codes": dict(sorted(old_violation_codes.items())),
            "qwen2_5_7b_4bit_gate_violation_codes": dict(sorted(new_violation_codes.items())),
            "model_call_count": len(all_calls),
            "model_latency_ms": sum(int(call["latency_ms"]) for call in all_calls),
            "max_peak_cuda_memory_bytes": max(
                [int(call.get("peak_cuda_memory_bytes") or 0) for call in all_calls] or [0]
            ),
        },
        "sections": section_reports,
        "calls": all_calls,
    }

    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[7B4bitAB] report={args.output}", flush=True)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
