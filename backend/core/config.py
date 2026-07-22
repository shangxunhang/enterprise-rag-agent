# =============================================================================
# 中文阅读说明：后端业务模块。
# 主要定义：_read_dotenv、_get_env、_to_bool、_to_int、_resolve_path、AppSettings、get_settings。建议先从公开入口函数开始，再沿调用关系向下阅读。
# =============================================================================
"""Application config.

Config v1 keeps the project configurable without introducing external dependencies.

Priority:
1. Environment variables
2. .env file in project root
3. Default values
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[2]


# 阅读注释（函数）：读取 dotenv。
def _read_dotenv(dotenv_path: Path) -> Dict[str, str]:
    """Read a simple .env file without python-dotenv dependency."""

    values: Dict[str, str] = {}

    if not dotenv_path.exists():
        return values

    for line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()

        if not line or line.startswith("#"):
            continue

        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        if key:
            values[key] = value

    return values


_DOTENV_VALUES = _read_dotenv(PROJECT_ROOT / ".env")


# 阅读注释（函数）：获取 env。
def _get_env(key: str, default: str) -> str:
    """获取 env。

    参数:
        key: key，具体约束请结合类型标注和调用方确认。
        default: default，具体约束请结合类型标注和调用方确认。

    返回:
        str

    阅读提示:
        主要直接调用：os.getenv, _DOTENV_VALUES.get。
    """
    return os.getenv(key) or _DOTENV_VALUES.get(key) or default


# 阅读注释（函数）：把 配置 转换为 bool。
def _to_bool(value: str | bool) -> bool:
    """把 配置 转换为 bool。

    参数:
        value: value，具体约束请结合类型标注和调用方确认。

    返回:
        bool

    阅读提示:
        主要直接调用：isinstance, lower, value.strip。
    """
    if isinstance(value, bool):
        return value

    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


# 阅读注释（函数）：把 配置 转换为 int。
def _to_int(value: str | int) -> int:
    """把 配置 转换为 int。

    参数:
        value: value，具体约束请结合类型标注和调用方确认。

    返回:
        int

    阅读提示:
        主要直接调用：isinstance, int。
    """
    if isinstance(value, int):
        return value

    return int(value)


# 阅读注释（函数）：解析并确定 路径。
def _resolve_path(value: str | Path) -> Path:
    """解析并确定 路径。

    参数:
        value: value，具体约束请结合类型标注和调用方确认。

    返回:
        Path

    阅读提示:
        主要直接调用：Path, path.is_absolute。
    """
    path = Path(value)

    if path.is_absolute():
        return path

    return PROJECT_ROOT / path


# 阅读注释（类）：封装 app settings，集中封装相关状态、依赖和行为。
@dataclass(frozen=True)
class AppSettings:
    """Application settings."""

    app_name: str
    app_env: str

    project_root: Path
    data_root: Path
    prompt_root: Path

    default_model_name: str
    default_scheme_prompt_id: str

    trace_enabled: bool
    data_capture_enabled: bool

    min_eval_output_chars: int

    # Supervisor LLM routing
    enable_llm_routing: bool
    supervisor_model_name: str

    # Local Qwen
    local_qwen_model_name: str
    local_qwen_model_path: Path
    local_qwen_device: str
    local_qwen_max_new_tokens: int

    # Multi-model profiles.  Legacy local_qwen_* above remains a compatibility
    # alias for the 1.5B profile until all callers use ModelRole.
    local_qwen_1_5b_model_name: str
    local_qwen_1_5b_model_path: Path
    local_qwen_3b_model_name: str
    local_qwen_3b_model_path: Path
    local_qwen_7b_model_name: str
    local_qwen_7b_model_path: Path

    # OpenAI-compatible remote provider.
    deepseek_model_name: str
    deepseek_provider_model_name: str
    deepseek_base_url: str
    deepseek_api_key: str

    @property
    def run_trace_dir(self) -> Path:
        """Derived trace directory; DATA_ROOT is the single configurable root."""
        return self.data_root / "runs"

    @property
    def data_capture_dir(self) -> Path:
        """Derived capture directory; DATA_ROOT is the single configurable root."""
        return self.data_root / "captures"

    @property
    def eval_output_dir(self) -> Path:
        """Derived evaluation output directory."""
        return self.data_root / "eval_outputs"

    @property
    def task_state_dir(self) -> Path:
        """Derived task-state directory."""
        return self.data_root / "tasks"

    @property
    def runtime_dir(self) -> Path:
        """Derived runtime coordination directory."""
        return self.data_root / "runtime"

    # 阅读注释（函数）：处理 as 字典 相关逻辑。
    def as_dict(self) -> dict:
        """处理 as 字典 相关逻辑。

        返回:
            dict

        阅读提示:
            主要直接调用：str。
        """
        return {
            "app_name": self.app_name,
            "app_env": self.app_env,
            "project_root": str(self.project_root),
            "data_root": str(self.data_root),
            "run_trace_dir": str(self.run_trace_dir),
            "data_capture_dir": str(self.data_capture_dir),
            "eval_output_dir": str(self.eval_output_dir),
            "task_state_dir": str(self.task_state_dir),
            "prompt_root": str(self.prompt_root),
            "default_model_name": self.default_model_name,
            "default_scheme_prompt_id": self.default_scheme_prompt_id,
            "trace_enabled": self.trace_enabled,
            "data_capture_enabled": self.data_capture_enabled,
            "min_eval_output_chars": self.min_eval_output_chars,
            "enable_llm_routing": self.enable_llm_routing,
            "supervisor_model_name": self.supervisor_model_name,
            "local_qwen_model_name": self.local_qwen_model_name,
            "local_qwen_model_path": str(self.local_qwen_model_path),
            "local_qwen_device": self.local_qwen_device,
            "local_qwen_max_new_tokens": self.local_qwen_max_new_tokens,
            "local_qwen_1_5b_model_name": self.local_qwen_1_5b_model_name,
            "local_qwen_1_5b_model_path": str(self.local_qwen_1_5b_model_path),
            "local_qwen_3b_model_name": self.local_qwen_3b_model_name,
            "local_qwen_3b_model_path": str(self.local_qwen_3b_model_path),
            "local_qwen_7b_model_name": self.local_qwen_7b_model_name,
            "local_qwen_7b_model_path": str(self.local_qwen_7b_model_path),
            "deepseek_model_name": self.deepseek_model_name,
            "deepseek_provider_model_name": self.deepseek_provider_model_name,
            "deepseek_base_url": self.deepseek_base_url,
            "deepseek_api_key_configured": bool(self.deepseek_api_key),
        }


_SETTINGS: Optional[AppSettings] = None


# 阅读注释（函数）：获取 settings。
def get_settings(reload: bool = False) -> AppSettings:
    """Get application settings.

    Args:
        reload: Rebuild settings from env and .env.
    """

    global _SETTINGS

    if _SETTINGS is not None and not reload:
        return _SETTINGS

    data_root = _resolve_path(_get_env("DATA_ROOT", "data"))
    default_model_name = _get_env("DEFAULT_MODEL_NAME", "fake_llm")

    settings = AppSettings(
        app_name=_get_env("APP_NAME", "agent-rag-system"),
        app_env=_get_env("APP_ENV", "dev"),
        project_root=PROJECT_ROOT,
        data_root=data_root,
        prompt_root=_resolve_path(_get_env("PROMPT_ROOT", "prompts")),

        default_model_name=default_model_name,
        default_scheme_prompt_id=_get_env(
            "DEFAULT_SCHEME_PROMPT_ID",
            "scheme_generation_v1",
        ),

        trace_enabled=_to_bool(_get_env("TRACE_ENABLED", "true")),
        data_capture_enabled=_to_bool(_get_env("DATA_CAPTURE_ENABLED", "true")),
        min_eval_output_chars=_to_int(_get_env("MIN_EVAL_OUTPUT_CHARS", "100")),

        enable_llm_routing=_to_bool(_get_env("ENABLE_LLM_ROUTING", "true")),
        supervisor_model_name=_get_env(
            "SUPERVISOR_MODEL_NAME",
            default_model_name,
        ),

        local_qwen_model_name=_get_env(
            "LOCAL_QWEN_MODEL_NAME",
            default_model_name,
        ),
        local_qwen_model_path=_resolve_path(
            _get_env(
                "LOCAL_QWEN_MODEL_PATH",
                "models/local_qwen",
            )
        ),
        local_qwen_device=_get_env("LOCAL_QWEN_DEVICE", "cuda"),
        local_qwen_max_new_tokens=_to_int(
            _get_env("LOCAL_QWEN_MAX_NEW_TOKENS", "1536")
        ),
        local_qwen_1_5b_model_name=_get_env(
            "LOCAL_QWEN_1_5B_MODEL_NAME",
            _get_env("LOCAL_QWEN_MODEL_NAME", "local_qwen2_5_1_5b"),
        ),
        local_qwen_1_5b_model_path=_resolve_path(
            _get_env(
                "LOCAL_QWEN_1_5B_MODEL_PATH",
                r"D:\models\huggingface\llm\Qwen2.5-1.5B-Instruct",
            )
        ),
        local_qwen_3b_model_name=_get_env(
            "LOCAL_QWEN_3B_MODEL_NAME",
            "local_qwen2_5_3b",
        ),
        local_qwen_3b_model_path=_resolve_path(
            _get_env(
                "LOCAL_QWEN_3B_MODEL_PATH",
                r"D:\models\huggingface\llm\Qwen2.5-3B-Instruct",
            )
        ),
        local_qwen_7b_model_name=_get_env(
            "LOCAL_QWEN_7B_MODEL_NAME",
            "local_qwen2_5_7b_gptq_int4",
        ),
        local_qwen_7b_model_path=_resolve_path(
            _get_env(
                "LOCAL_QWEN_7B_MODEL_PATH",
                r"D:\models\huggingface\llm\Qwen2.5-7B-Instruct-GPTQ-Int4",
            )
        ),
        deepseek_model_name=_get_env("DEEPSEEK_MODEL_NAME", "deepseek_api"),
        deepseek_provider_model_name=_get_env(
            "DEEPSEEK_PROVIDER_MODEL_NAME", "deepseek-chat"
        ),
        deepseek_base_url=_get_env(
            "DEEPSEEK_BASE_URL", "https://api.deepseek.com"
        ),
        deepseek_api_key=_get_env("DEEPSEEK_API_KEY", ""),
    )

    _SETTINGS = settings
    return settings
