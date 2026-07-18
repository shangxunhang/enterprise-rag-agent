import json
import sys
from pathlib import Path


def read_plugin_info(trace_path: str) -> None:
    path = Path(trace_path)

    print()
    print("=" * 80)
    print(f"Trace: {path.name}")

    parse_errors = 0

    with path.open("r", encoding="utf-8-sig") as file:
        for line_number, line in enumerate(file, start=1):
            line = line.strip()

            if not line:
                continue

            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                parse_errors += 1
                continue

            if event.get("component_name") != "RealRAGTool":
                continue

            payload = event.get("payload") or {}
            tool_result = payload.get("tool_result") or {}
            result = tool_result.get("result") or {}

            trace = result.get("trace") or {}
            trace_extra = trace.get("extra") or {}
            metadata = trace_extra.get("rag_result_metadata") or {}

            if not metadata:
                continue

            components = metadata.get("pipeline_components") or {}
            component = components.get("context_packer") or {}

            context = result.get("context") or {}
            context_extra = context.get("extra") or {}

            print(f"Line:                    {line_number}")
            print(f"Config file:             {metadata.get('pipeline_config_file')}")
            print(f"Config hash:             {metadata.get('pipeline_config_hash')}")
            print(f"Component category:      {component.get('category')}")
            print(f"Component name:          {component.get('name')}")
            print(f"Component version:       {component.get('version')}")
            print(f"Implementation:          {component.get('implementation')}")
            print(f"Actual packing strategy: {context_extra.get('packing_strategy')}")
            print(f"RAG status:              {result.get('status')}")
            print(f"JSON parse errors:       {parse_errors}")
            return

    print("未找到 RAG 插件元数据")
    print(f"JSON parse errors: {parse_errors}")


for item in sys.argv[1:]:
    read_plugin_info(item)
