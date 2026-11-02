"""加载并缓存应用运行配置。

默认 legacy_dotenv_v1 保持进程环境变量、项目根目录 ``.env``、代码默认值的
优先级。首次导入前可通过 RAG_CONFIG_SOURCE_POLICY 显式选择
process_env_defaults_v1，完全不读取 dotenv，仅使用进程环境与默认值。
来源策略在导入时固定，运行中改变策略会拒绝；相对路径仍以项目根目录解析。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional


# 以当前文件向上两级定位仓库根目录，作为 .env 和相对路径的统一基准。
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _read_dotenv(dotenv_path: Path) -> Dict[str, str]:
    """读取项目支持的简单 ``KEY=VALUE`` 格式 .env 文件。

    Args:
        dotenv_path: 要读取的 .env 文件路径。

    Returns:
        按文件顺序解析出的键值字典；文件不存在时返回空字典。

    解析器忽略空行、整行注释和不含等号的行，并去除值两侧的一层单引号或双引号。
    它不实现变量展开、多行值或 shell 语法。
    """

    # 第一步：建立独立结果字典，避免调用方获得内部共享可变对象。
    values: Dict[str, str] = {}

    # 第二步：没有 .env 时直接返回，配置仍可由环境变量和默认值组成。
    if not dotenv_path.exists():
        return values

    # 第三步：逐行解析文本，不执行其中任何 shell 表达式。
    for line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()

        # 跳过空行和整行注释。
        if not line or line.startswith("#"):
            continue

        # 不符合 KEY=VALUE 最小格式的行不参与配置。
        if "=" not in line:
            continue

        # 只按第一个等号拆分，使 URL 等值中可以继续包含等号。
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        # 空键没有可寻址意义，因此忽略；空值本身允许保留。
        if key:
            values[key] = value

    return values


# Validate the source selector before even probing dotenv. Legacy callers retain
# their one-time parse; process-only callers never invoke the dotenv reader.
CONFIG_SOURCE_POLICY_ENV = "RAG_CONFIG_SOURCE_POLICY"
CONFIG_SOURCE_POLICY = os.environ.get(CONFIG_SOURCE_POLICY_ENV, "legacy_dotenv_v1")
if CONFIG_SOURCE_POLICY not in {"legacy_dotenv_v1", "process_env_defaults_v1"}:
    raise ValueError("Unsupported application configuration source policy")
_DOTENV_VALUES = (_read_dotenv(PROJECT_ROOT / ".env")
                  if CONFIG_SOURCE_POLICY == "legacy_dotenv_v1" else {})


def _get_env(key: str, default: str) -> str:
    """按固定优先级读取一个字符串配置值。

    Args:
        key: 环境变量和 .env 使用的配置键。
        default: 前两层均没有非空值时使用的默认字符串。

    Returns:
        环境变量、.env 值或默认值中的第一个非空值。
    """

    # ``or`` 表示显式空字符串不会覆盖后续来源，适合历史配置语义。
    return os.getenv(key) or _DOTENV_VALUES.get(key) or default


def _get_explicit_env(key: str, default: str) -> str:
    """按来源存在性读取严格配置，使显式空值也进入后续验证。"""

    if key in os.environ:
        return os.environ[key]
    if key in _DOTENV_VALUES:
        return _DOTENV_VALUES[key]
    return default


def _to_bool(value: str | bool) -> bool:
    """把布尔值或常见真值字符串转换为 ``bool``。

    Args:
        value: 原生布尔值或配置字符串。

    Returns:
        原生布尔值保持不变；字符串属于 1/true/yes/y/on 时返回 ``True``，其他值
        返回 ``False``。
    """

    # 已经是布尔值时不再执行字符串转换。
    if isinstance(value, bool):
        return value

    # 统一去除空白并忽略大小写后按允许集合判断。
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _to_int(value: str | int) -> int:
    """把整数或十进制配置字符串转换为 ``int``。

    Args:
        value: 原生整数或可由 ``int`` 解析的字符串。

    Returns:
        规范整数值。

    Raises:
        ValueError: 字符串不是合法整数时由 ``int`` 直接抛出。
    """

    # 避免对已经规范的整数重复转换。
    if isinstance(value, int):
        return value

    # 保留 Python int 的严格失败行为，不静默回退到默认值。
    return int(value)


def _bounded_int(value: str | int, *, key: str, minimum: int) -> int:
    """解析带下界约束的整数配置，并对非法显式值 fail fast。"""

    resolved = _to_int(value)
    if resolved < minimum:
        raise ValueError(f"{key} must be >= {minimum}, got {resolved}")
    return resolved


def _to_float(value: str | float) -> float:
    if isinstance(value, float):
        return value
    return float(value)


def _bounded_float(
    value: str | float,
    *,
    key: str,
    minimum: float,
    maximum: float | None = None,
) -> float:
    resolved = _to_float(value)
    if resolved < minimum or (maximum is not None and resolved > maximum):
        suffix = f" and <= {maximum}" if maximum is not None else ""
        raise ValueError(
            f"{key} must be >= {minimum}{suffix}, got {resolved}"
        )
    return resolved


def _gate_enforcement_mode(value: str) -> str:
    """规范并严格验证 Gate V2 的 occurrence-level enforcement mode。"""

    resolved = str(value).strip().lower()
    if resolved not in {"strict", "shadow"}:
        raise ValueError(
            "GATE_ENFORCEMENT_MODE must be one of: strict, shadow; "
            f"got {value!r}"
        )
    return resolved


def _resolve_path(value: str | Path) -> Path:
    """把配置路径解析为项目可使用的绝对或根目录相对路径。

    Args:
        value: 绝对路径或相对于项目根目录的路径。

    Returns:
        绝对输入原样返回；相对输入拼接到 ``PROJECT_ROOT``。
    """

    # 第一步：统一转换为 Path，后续调用方不再处理字符串路径差异。
    path = Path(value)

    # 第二步：绝对路径通常来自部署环境或本地模型配置，直接保留。
    if path.is_absolute():
        return path

    # 第三步：相对路径统一以仓库根目录为基准，不依赖进程当前工作目录。
    return PROJECT_ROOT / path


@dataclass(frozen=True)
class AppSettings:
    """保存应用启动后使用的不可变配置快照。

    字段按应用、目录、默认模型、可观测性、本地模型档位和远程供应商分组。配置对象
    不负责创建目录或加载模型，只向装配层提供经过类型转换的值。
    """

    # 应用显示和日志名称。
    app_name: str
    # 部署环境标识，例如 dev、test 或 prod。
    app_env: str

    # 仓库根目录。
    project_root: Path
    # 所有运行轨迹、采集、评测和状态产物的单一数据根目录。
    data_root: Path
    # 提示词模板文件根目录。
    prompt_root: Path

    # 没有显式角色路由时使用的默认模型注册名。
    default_model_name: str
    # 方案生成默认提示词模板标识。
    default_scheme_prompt_id: str

    # 是否写入结构化运行轨迹。
    trace_enabled: bool
    # 是否采集原始交互、SFT 候选和评测样本。
    data_capture_enabled: bool

    # 评测输出被认为具有最小有效内容所需的字符数。
    min_eval_output_chars: int

    # 同一 durable run 允许进入受控恢复的最大次数。
    max_recovery_attempts: int

    # 业务门禁失败后的统一执行模式：strict 阻断，shadow 记录后继续。
    gate_enforcement_mode: str
    # DENY 触发的 Step-5 Evidence Recovery 最大逻辑轮数；初始检索不计入。
    gate_recovery_rounds_limit: int
    # 连续语义 NO_PROGRESS Recovery 轮数上限。
    gate_no_progress_rounds_limit: int

    # 是否允许 Supervisor 使用模型进行工作流路由。
    enable_llm_routing: bool
    # Supervisor 路由角色使用的模型注册名。
    supervisor_model_name: str

    # 历史本地 Qwen 兼容模型注册名。
    local_qwen_model_name: str
    # 历史本地 Qwen 兼容模型目录。
    local_qwen_model_path: Path
    # 本地推理设备，例如 cuda 或 cpu。
    local_qwen_device: str
    # 本地模型单次生成默认最大新 token 数。
    local_qwen_max_new_tokens: int

    # 1.5B 本地模型档位的注册名；旧 local_qwen_* 字段目前兼容映射到该档位。
    local_qwen_1_5b_model_name: str
    # 1.5B 本地模型文件目录。
    local_qwen_1_5b_model_path: Path
    # 3B 本地模型档位的注册名。
    local_qwen_3b_model_name: str
    # 3B 本地模型文件目录。
    local_qwen_3b_model_path: Path
    # 7B 本地模型档位的注册名。
    local_qwen_7b_model_name: str
    # 7B 本地模型文件目录。
    local_qwen_7b_model_path: Path

    # M7 Durable Run Memory PostgreSQL operational settings.
    memory_database_url: str
    memory_database_pool_min_size: int
    memory_database_pool_max_size: int
    memory_database_statement_timeout_ms: int
    memory_database_schema_version: str

    # Frozen SIF-13 retention defaults.
    raw_model_retention_days: int
    raw_model_diagnostic_retention_days: int
    raw_tool_retention_days: int
    raw_tool_diagnostic_retention_days: int
    old_artifact_retention_days: int
    claim_verification_history_retention_days: int
    memory_historical_retention_days: int
    memory_ineligible_quarantine_days: int
    context_replay_retention_days: int
    context_replay_diagnostic_retention_days: int

    # Frozen SIF-13 local-storage pressure thresholds.
    disk_soft_free_ratio: float
    disk_soft_free_gib: int
    disk_hard_free_ratio: float
    disk_hard_free_gib: int
    disk_critical_free_ratio: float
    disk_critical_free_gib: int

    # 项目内部使用的 DeepSeek 模型注册名。
    deepseek_model_name: str
    # 发送给 OpenAI 兼容供应商接口的真实模型名称。
    deepseek_provider_model_name: str
    # DeepSeek OpenAI 兼容 API 基础地址。
    deepseek_base_url: str
    # 是否启用 DeepSeek V4 思考模式；Writer A/B 默认关闭以控制变量。
    deepseek_thinking_enabled: bool
    # SECTION_GENERATION 角色的可选单模型 profile override；空值保留默认路由。
    section_generation_model_profile: str
    # DeepSeek API 密钥；不得通过日志或 as_dict 明文输出。
    deepseek_api_key: str
    # Versioned provider/model capability catalog; never contains credentials.
    model_capabilities_path: Path | None = None
    # Optional remote operational allowance; absent preserves the legacy policy.
    deepseek_max_output_tokens: int | None = None

    @property
    def run_trace_dir(self) -> Path:
        """返回运行轨迹和运行级聚合产物目录。"""

        # DATA_ROOT 是唯一可配置根目录，避免各模块分别配置相互漂移的路径。
        return self.data_root / "runs"

    @property
    def data_capture_dir(self) -> Path:
        """返回训练与评测数据采集根目录。"""

        return self.data_root / "captures"

    @property
    def eval_output_dir(self) -> Path:
        """返回离线或端到端评测输出目录。"""

        return self.data_root / "eval_outputs"

    @property
    def task_state_dir(self) -> Path:
        """返回任务生命周期状态持久化目录。"""

        return self.data_root / "tasks"

    @property
    def runtime_dir(self) -> Path:
        """返回检查点、锁和其他运行协调产物目录。"""

        return self.data_root / "runtime"

    def as_dict(self) -> dict:
        """返回适合日志和诊断展示的可序列化配置摘要。

        Returns:
            路径已转为字符串、API 密钥只暴露是否配置的字典。
        """

        # 逐字段显式构造白名单，避免以后新增敏感字段时被数据类整体序列化泄漏。
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
            "max_recovery_attempts": self.max_recovery_attempts,
            "gate_enforcement_mode": self.gate_enforcement_mode,
            "gate_recovery_rounds_limit": self.gate_recovery_rounds_limit,
            "gate_no_progress_rounds_limit": self.gate_no_progress_rounds_limit,
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
            "memory_database_configured": bool(self.memory_database_url),
            "memory_database_pool_min_size": self.memory_database_pool_min_size,
            "memory_database_pool_max_size": self.memory_database_pool_max_size,
            "memory_database_statement_timeout_ms": self.memory_database_statement_timeout_ms,
            "memory_database_schema_version": self.memory_database_schema_version,
            "raw_model_retention_days": self.raw_model_retention_days,
            "raw_model_diagnostic_retention_days": self.raw_model_diagnostic_retention_days,
            "raw_tool_retention_days": self.raw_tool_retention_days,
            "raw_tool_diagnostic_retention_days": self.raw_tool_diagnostic_retention_days,
            "old_artifact_retention_days": self.old_artifact_retention_days,
            "claim_verification_history_retention_days": self.claim_verification_history_retention_days,
            "memory_historical_retention_days": self.memory_historical_retention_days,
            "memory_ineligible_quarantine_days": self.memory_ineligible_quarantine_days,
            "context_replay_retention_days": self.context_replay_retention_days,
            "context_replay_diagnostic_retention_days": self.context_replay_diagnostic_retention_days,
            "disk_soft_free_ratio": self.disk_soft_free_ratio,
            "disk_soft_free_gib": self.disk_soft_free_gib,
            "disk_hard_free_ratio": self.disk_hard_free_ratio,
            "disk_hard_free_gib": self.disk_hard_free_gib,
            "disk_critical_free_ratio": self.disk_critical_free_ratio,
            "disk_critical_free_gib": self.disk_critical_free_gib,
            "deepseek_model_name": self.deepseek_model_name,
            "deepseek_provider_model_name": self.deepseek_provider_model_name,
            "deepseek_base_url": self.deepseek_base_url,
            "deepseek_thinking_enabled": self.deepseek_thinking_enabled,
            "deepseek_max_output_tokens": self.deepseek_max_output_tokens,
            "section_generation_model_profile": self.section_generation_model_profile,
            # 只说明是否配置，不返回密钥、长度或任何可推测内容。
            "deepseek_api_key_configured": bool(self.deepseek_api_key),
            "model_capabilities_path": str(self.model_capabilities_path) if self.model_capabilities_path else None,
        }


# 缓存当前进程使用的不可变设置，避免每个组件重复读取环境和转换路径。
_SETTINGS: Optional[AppSettings] = None


def get_settings(reload: bool = False) -> AppSettings:
    """获取当前进程的应用配置快照。

    Args:
        reload: 为 ``True`` 时忽略内存缓存并重新读取环境变量与已解析 .env 值。

    Returns:
        当前不可变 ``AppSettings``。
    """

    global _SETTINGS

    if os.environ.get(CONFIG_SOURCE_POLICY_ENV, "legacy_dotenv_v1") != CONFIG_SOURCE_POLICY:
        raise ValueError("Application configuration source policy drift")

    # 第一步：默认复用缓存，保证同一进程中的组件看到一致配置快照。
    if _SETTINGS is not None and not reload:
        return _SETTINGS

    # 第二步：先解析被多个字段复用的数据根目录和默认模型名称。
    data_root = _resolve_path(_get_env("DATA_ROOT", "data"))
    default_model_name = _get_env("DEFAULT_MODEL_NAME", "fake_llm")

    # 第三步：按字段读取、类型转换并构造不可变设置对象。
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
        max_recovery_attempts=_to_int(_get_env("MAX_RECOVERY_ATTEMPTS", "3")),
        gate_enforcement_mode=_gate_enforcement_mode(
            _get_explicit_env("GATE_ENFORCEMENT_MODE", "strict")
        ),
        gate_recovery_rounds_limit=_bounded_int(
            _get_explicit_env("GATE_RECOVERY_ROUNDS_LIMIT", "2"),
            key="GATE_RECOVERY_ROUNDS_LIMIT",
            minimum=0,
        ),
        gate_no_progress_rounds_limit=_bounded_int(
            _get_explicit_env("GATE_NO_PROGRESS_ROUNDS_LIMIT", "1"),
            key="GATE_NO_PROGRESS_ROUNDS_LIMIT",
            minimum=1,
        ),
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
        memory_database_url=_get_explicit_env("MEMORY_DATABASE_URL", "").strip(),
        memory_database_pool_min_size=_bounded_int(
            _get_explicit_env("MEMORY_DATABASE_POOL_MIN_SIZE", "1"),
            key="MEMORY_DATABASE_POOL_MIN_SIZE",
            minimum=1,
        ),
        memory_database_pool_max_size=_bounded_int(
            _get_explicit_env("MEMORY_DATABASE_POOL_MAX_SIZE", "5"),
            key="MEMORY_DATABASE_POOL_MAX_SIZE",
            minimum=1,
        ),
        memory_database_statement_timeout_ms=_bounded_int(
            _get_explicit_env("MEMORY_DATABASE_STATEMENT_TIMEOUT_MS", "5000"),
            key="MEMORY_DATABASE_STATEMENT_TIMEOUT_MS",
            minimum=1,
        ),
        memory_database_schema_version=_get_explicit_env(
            "MEMORY_DATABASE_SCHEMA_VERSION", "memory_v1"
        ).strip(),
        raw_model_retention_days=_bounded_int(
            _get_explicit_env("RAW_MODEL_RETENTION_DAYS", "7"),
            key="RAW_MODEL_RETENTION_DAYS",
            minimum=0,
        ),
        raw_model_diagnostic_retention_days=_bounded_int(
            _get_explicit_env("RAW_MODEL_DIAGNOSTIC_RETENTION_DAYS", "14"),
            key="RAW_MODEL_DIAGNOSTIC_RETENTION_DAYS",
            minimum=0,
        ),
        raw_tool_retention_days=_bounded_int(
            _get_explicit_env("RAW_TOOL_RETENTION_DAYS", "3"),
            key="RAW_TOOL_RETENTION_DAYS",
            minimum=0,
        ),
        raw_tool_diagnostic_retention_days=_bounded_int(
            _get_explicit_env("RAW_TOOL_DIAGNOSTIC_RETENTION_DAYS", "14"),
            key="RAW_TOOL_DIAGNOSTIC_RETENTION_DAYS",
            minimum=0,
        ),
        old_artifact_retention_days=_bounded_int(
            _get_explicit_env("OLD_ARTIFACT_RETENTION_DAYS", "30"),
            key="OLD_ARTIFACT_RETENTION_DAYS",
            minimum=0,
        ),
        claim_verification_history_retention_days=_bounded_int(
            _get_explicit_env("CLAIM_VERIFICATION_HISTORY_RETENTION_DAYS", "90"),
            key="CLAIM_VERIFICATION_HISTORY_RETENTION_DAYS",
            minimum=0,
        ),
        memory_historical_retention_days=_bounded_int(
            _get_explicit_env("MEMORY_HISTORICAL_RETENTION_DAYS", "30"),
            key="MEMORY_HISTORICAL_RETENTION_DAYS",
            minimum=0,
        ),
        memory_ineligible_quarantine_days=_bounded_int(
            _get_explicit_env("MEMORY_INELIGIBLE_QUARANTINE_DAYS", "7"),
            key="MEMORY_INELIGIBLE_QUARANTINE_DAYS",
            minimum=0,
        ),
        context_replay_retention_days=_bounded_int(
            _get_explicit_env("CONTEXT_REPLAY_RETENTION_DAYS", "7"),
            key="CONTEXT_REPLAY_RETENTION_DAYS",
            minimum=0,
        ),
        context_replay_diagnostic_retention_days=_bounded_int(
            _get_explicit_env("CONTEXT_REPLAY_DIAGNOSTIC_RETENTION_DAYS", "14"),
            key="CONTEXT_REPLAY_DIAGNOSTIC_RETENTION_DAYS",
            minimum=0,
        ),
        disk_soft_free_ratio=_bounded_float(
            _get_explicit_env("DISK_SOFT_FREE_RATIO", "0.15"),
            key="DISK_SOFT_FREE_RATIO",
            minimum=0.0,
            maximum=1.0,
        ),
        disk_soft_free_gib=_bounded_int(
            _get_explicit_env("DISK_SOFT_FREE_GIB", "20"),
            key="DISK_SOFT_FREE_GIB",
            minimum=0,
        ),
        disk_hard_free_ratio=_bounded_float(
            _get_explicit_env("DISK_HARD_FREE_RATIO", "0.10"),
            key="DISK_HARD_FREE_RATIO",
            minimum=0.0,
            maximum=1.0,
        ),
        disk_hard_free_gib=_bounded_int(
            _get_explicit_env("DISK_HARD_FREE_GIB", "10"),
            key="DISK_HARD_FREE_GIB",
            minimum=0,
        ),
        disk_critical_free_ratio=_bounded_float(
            _get_explicit_env("DISK_CRITICAL_FREE_RATIO", "0.05"),
            key="DISK_CRITICAL_FREE_RATIO",
            minimum=0.0,
            maximum=1.0,
        ),
        disk_critical_free_gib=_bounded_int(
            _get_explicit_env("DISK_CRITICAL_FREE_GIB", "5"),
            key="DISK_CRITICAL_FREE_GIB",
            minimum=0,
        ),
        deepseek_model_name=_get_env("DEEPSEEK_MODEL_NAME", "deepseek_api"),
        deepseek_provider_model_name=_get_env(
            "DEEPSEEK_PROVIDER_MODEL_NAME", "deepseek-v4-flash"
        ),
        deepseek_base_url=_get_env(
            "DEEPSEEK_BASE_URL", "https://api.deepseek.com"
        ),
        deepseek_thinking_enabled=_to_bool(
            _get_env("DEEPSEEK_THINKING_ENABLED", "false")
        ),
        section_generation_model_profile=_get_env(
            "SECTION_GENERATION_MODEL_PROFILE", ""
        ).strip(),
        deepseek_api_key=_get_env("DEEPSEEK_API_KEY", ""),
        model_capabilities_path=_resolve_path(_get_env("MODEL_CAPABILITIES_PATH", "config/model_capabilities.json")),
        deepseek_max_output_tokens=(
            _bounded_int(
                _get_explicit_env("DEEPSEEK_MAX_OUTPUT_TOKENS", ""),
                key="DEEPSEEK_MAX_OUTPUT_TOKENS",
                minimum=1,
            )
            if "DEEPSEEK_MAX_OUTPUT_TOKENS" in os.environ
            or "DEEPSEEK_MAX_OUTPUT_TOKENS" in _DOTENV_VALUES
            else None
        ),
    )

    # M7/SIF-13 operational invariants fail fast at the unique config owner.
    if not settings.memory_database_schema_version:
        raise ValueError("MEMORY_DATABASE_SCHEMA_VERSION must be non-empty")
    if settings.memory_database_pool_min_size > settings.memory_database_pool_max_size:
        raise ValueError(
            "MEMORY_DATABASE_POOL_MIN_SIZE must be <= MEMORY_DATABASE_POOL_MAX_SIZE"
        )
    if not (
        settings.disk_soft_free_ratio
        > settings.disk_hard_free_ratio
        > settings.disk_critical_free_ratio
    ):
        raise ValueError(
            "disk free-ratio thresholds must satisfy SOFT > HARD > CRITICAL"
        )
    if not (
        settings.disk_soft_free_gib
        > settings.disk_hard_free_gib
        > settings.disk_critical_free_gib
    ):
        raise ValueError(
            "disk free-GiB thresholds must satisfy SOFT > HARD > CRITICAL"
        )

    # 第四步：发布新快照到进程缓存并返回同一对象。
    _SETTINGS = settings
    return settings
