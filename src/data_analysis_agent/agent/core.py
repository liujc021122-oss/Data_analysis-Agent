# -*- coding: utf-8 -*-
"""
简化的 Notebook 数据分析智能体
仅包含用户和助手两个角2. 图片必须保存到指定的会话目录中，输出绝对路径，禁止使用plt.show()
3. 表格输出控制：超过15行只显示前5行和后5行
4. 强制使用SimHei字体：plt.rcParams['font.sans-serif'] = ['SimHei']
5. 输出格式严格使用YAML共享上下文的单轮对话模式
"""

import os
import json
import logging
import re
import shutil
import yaml
import pandas
import asyncio
import inspect
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence
from uuid import UUID, uuid4

from pydantic import ValidationError

from ..config.llm import LLMConfig
from ..config import build_storage
from ..config.settings import ConfigurationError, Settings, load_settings
from ..domain.errors import EvidenceError
from ..domain.enums import EvidenceVerificationStatus, ReportFormat, TaskStatus
from ..domain.models import ChartArtifact, EvidenceClaim, MetricArtifact
from ..execution import (
    AgentExecutionSession,
    CodeExecutionBackend,
    CodeExecutor,
    ExecutionLimits,
    NetworkPolicy,
    build_execution_backend,
)
from ..execution.runtime import ContainerRuntime
from ..llm import LLMStructuredOutputError
from ..reports import ReportDocument, ReportService
from ..reports.service import default_renderers
from ..reports.word import WordReportRenderer, generate_word_report
from ..services.llm import LLMHelper
from ..services.errors import sanitize_exception
from ..services.responses import extract_code_from_response, format_execution_result
from ..services.session import create_session_output_dir
from ..services.evidence import EvidenceRegistry
from ..datasets import (
    CsvInspector,
    DatasetAccessDeniedError,
    DatasetErrorCode,
    DatasetResolver,
    DatasetUploadService,
    InMemoryDatasetStore,
    LocalStorageBackend,
)
from ..datasets.errors import UploadValidationError
from ..persistence.models import ArtifactRecord, ReportRecord
from ..storage import ArtifactStorageService, Storage, StorageLifecycleService
from ..storage.errors import StorageError, StorageErrorCode
from ..tools import ToolCallRequest, ToolCallResult, ToolContext, ToolExecutor, ToolRegistry
from .llm_port import AgentLLMPort
from .legacy_adapter import LegacyAnalysisAdapter
from .orchestration_errors import AgentOrchestrationError
from .orchestrator import AgentOrchestrator
from .prompts import final_report_system_prompt
from .schemas import AgentAction


class _EphemeralArtifactRepository:
    def __init__(self) -> None:
        self.records: dict[UUID, ArtifactRecord] = {}

    def add(self, record: ArtifactRecord) -> ArtifactRecord:
        self.records[record.artifact_id] = record
        return record

    def get(self, artifact_id: UUID) -> ArtifactRecord | None:
        return self.records.get(artifact_id)

    def get_for_user(self, artifact_id: UUID, user_id: UUID) -> ArtifactRecord | None:
        return self.records.get(artifact_id)

    def list_for_task(self, task_id: UUID) -> list[ArtifactRecord]:
        return [record for record in self.records.values() if record.task_id == task_id]


class _EphemeralReportRepository:
    def __init__(self) -> None:
        self.records: dict[UUID, ReportRecord] = {}

    def add(self, record: ReportRecord) -> ReportRecord:
        self.records[record.report_id] = record
        return record


@contextmanager
def _upload_compatibility_files(
    files: Sequence[str], *, settings: Settings
) -> Iterator[tuple[tuple[UUID, ...], DatasetResolver, UUID]]:
    if settings.app_env == "production":
        raise ConfigurationError(
            "production files uploads require the production upload service; "
            "use that service or pass dataset_ids"
        )

    owner_id = uuid4()
    storage = LocalStorageBackend(settings.storage_local_root)
    metadata_store = InMemoryDatasetStore()
    upload_service = DatasetUploadService(
        storage=storage,
        inspector=CsvInspector(),
        metadata_store=metadata_store,
        max_upload_size=settings.max_upload_size,
    )
    uploaded_ids: list[UUID] = []
    try:
        for file_name in files:
            file_path = Path(file_name)
            with file_path.open("rb") as stream:
                uploaded = upload_service.upload(
                    stream,
                    original_filename=file_path.name,
                    owner_id=owner_id,
                )
                uploaded_ids.append(uploaded.dataset_id)
        yield (
            tuple(uploaded_ids),
            DatasetResolver(storage=storage, metadata_store=metadata_store),
            owner_id,
        )
    finally:
        for dataset_id in uploaded_ids:
            record = metadata_store.get_for_user(dataset_id, owner_id)
            try:
                storage.delete(record.source_uri)
            except Exception:
                # Do not log storage paths, URIs, or exception messages.
                logging.getLogger(__name__).warning("Temporary dataset cleanup failed")


class DataAnalysisAgent:
    """
    数据分析智能体

    职责：
    - 接收用户自然语言需求
    - 生成Python分析代码
    - 执行代码并收集结果
    - 基于执行结果继续生成后续分析代码
    """
    def __init__(
        self,
        llm_config: LLMConfig = None,
        output_dir: str = "outputs",
        max_rounds: int = 20,
        generate_word_report: bool = True,
        dataset_resolver: DatasetResolver | None = None,
        dataset_owner_id: UUID | None = None,
        storage: Storage | None = None,
        artifact_storage: ArtifactStorageService | None = None,
        task_id: UUID | None = None,
        llm: Any | None = None,
        tool_registry: ToolRegistry | None = None,
        tool_executor: ToolExecutor | None = None,
        settings: Settings | None = None,
        execution_backend: CodeExecutionBackend | None = None,
        execution_runtime: ContainerRuntime | None = None,
        evidence_registry: EvidenceRegistry | None = None,
        *,
        report_service: ReportService | None = None,
    ):
        """
        初始化智能体

        Args:
            config: LLM配置
            output_dir: 输出目录
            max_rounds: 最大对话轮数
            generate_word_report: 是否同时生成Word报告
        """
        self.config = llm_config or LLMConfig()
        self._owns_llm = llm is None
        self._llm_closed = False
        self.llm = llm if llm is not None else LLMHelper(self.config)
        self.llm_port = AgentLLMPort(self.llm, self.config)
        if (
            tool_registry is not None
            and tool_executor is not None
            and tool_executor.registry is not tool_registry
        ):
            raise ValueError("tool_executor and tool_registry must use the same registry")
        self.tool_executor = (
            ToolExecutor(tool_registry)
            if tool_registry is not None and tool_executor is None
            else tool_executor
        )
        self.base_output_dir = output_dir
        self.settings = settings or load_settings()
        self._provided_execution_backend = execution_backend
        self._execution_runtime = execution_runtime
        self._provided_evidence_registry = evidence_registry
        self.max_rounds = max_rounds
        self.generate_word_report = generate_word_report
        self.dataset_resolver = dataset_resolver
        self.dataset_owner_id = dataset_owner_id
        self.storage = storage
        self.artifact_storage = artifact_storage
        self._provided_task_id = task_id or (
            evidence_registry.task_id if evidence_registry is not None else None
        )
        self.task_id = self._provided_task_id
        self.storage_lifecycle = None
        if self.storage is not None and self.artifact_storage is None:
            self.artifact_storage = ArtifactStorageService(
                storage=self.storage,
                artifact_repository=_EphemeralArtifactRepository(),
                report_repository=_EphemeralReportRepository(),
            )
        if self.storage is not None:
            self.storage_lifecycle = StorageLifecycleService(
                storage=self.storage,
                artifact_storage=self.artifact_storage,
                output_root=self.base_output_dir,
            )
        # 对话历史和上下文
        self.conversation_history = []
        self.analysis_results = []
        self.current_round = 0
        self.task_id = self._provided_task_id or uuid4()
        self.evidence_registry = evidence_registry or EvidenceRegistry(
            task_id=self.task_id,
            output_root=self.base_output_dir,
        )
        self._report_service_is_injected = report_service is not None
        self.report_service = report_service
        self.artifact_records: list[ArtifactRecord] = []
        self.execution_audits: list[dict[str, Any]] = []
        self._last_execution_id: UUID | None = None
        self._last_code_hash: str | None = None
        self.storage_error = None
        self.session_output_dir = None
        self.executor = None
        self.orchestrator = None

    def _build_execution_session(self):
        """Create the typed execution facade for production or explicit injection."""
        settings = getattr(self, "settings", None)
        backend = getattr(self, "_provided_execution_backend", None)
        use_typed_backend = backend is not None or (
            settings is not None and getattr(settings, "app_env", None) == "production"
        )
        if not use_typed_backend:
            return CodeExecutor(self.session_output_dir)

        if backend is None:
            backend = build_execution_backend(
                settings,
                runtime=getattr(self, "_execution_runtime", None),
            )
        if (
            settings is not None
            and getattr(settings, "app_env", None) == "production"
            and not bool(getattr(backend, "production_safe", False))
        ):
            raise ConfigurationError(
                "production requires a production-safe execution backend"
            )
        timeout_seconds = 300.0
        network_policy = NetworkPolicy.DISABLED
        if settings is not None:
            timeout_seconds = min(
                float(getattr(settings, "max_task_runtime", timeout_seconds)),
                3600.0,
            )
            if getattr(settings, "execution_network_mode", "none") == "bridge":
                network_policy = NetworkPolicy.ENABLED
        return AgentExecutionSession(
            backend=backend,
            output_dir=self.session_output_dir,
            output_scope=self.base_output_dir,
            task_id=self.task_id,
            limits=ExecutionLimits(timeout_seconds=timeout_seconds),
            network_policy=network_policy,
        )

    def _close_execution_session(self) -> None:
        executor = getattr(self, "executor", None)
        close = getattr(executor, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                logger = logging.getLogger(__name__)
                logger.warning("Execution session cleanup failed; retrying")
                try:
                    close()
                except Exception:
                    logger.error("Execution session cleanup retry failed")

    def execute_tool(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        permissions: Iterable[str] = (),
        network_allowed: bool = False,
    ) -> ToolCallResult:
        """Execute an opt-in typed tool and record its agent-facing result."""
        if self.tool_executor is None:
            raise RuntimeError(
                "tool executor is not configured; pass tool_registry or tool_executor"
            )

        request = ToolCallRequest(
            task_id=self.task_id,
            tool_name=tool_name,
            arguments=dict(arguments),
        )
        context = ToolContext(
            task_id=self.task_id,
            user_id=self.dataset_owner_id,
            permissions=frozenset(permissions),
            network_allowed=network_allowed,
        )
        result = self.tool_executor.execute(request, context)
        self.conversation_history.append(result.to_agent_payload())
        return result

    def _close_owned_llm(self) -> None:
        """Close only the LLM created by this Agent instance."""
        if not getattr(self, "_owns_llm", False) or getattr(self, "_llm_closed", False):
            return
        self._llm_closed = True
        close = getattr(getattr(self, "llm", None), "close", None)
        if not callable(close):
            return
        try:
            result = close()
            if inspect.isawaitable(result):
                asyncio.run(result)
        except Exception:
            logging.getLogger(__name__).warning("Owned LLM cleanup failed")

    def _get_llm_port(self) -> AgentLLMPort:
        port = getattr(self, "llm_port", None)
        if port is None:
            port = AgentLLMPort(self.llm, self.config)
            self.llm_port = port
        return port

    def _get_evidence_registry(self) -> EvidenceRegistry:
        registry = getattr(self, "evidence_registry", None)
        if registry is None:
            task_id = getattr(self, "task_id", None)
            if task_id is None:
                task_id = uuid4()
                self.task_id = task_id
            registry = EvidenceRegistry(
                task_id=task_id,
                output_root=getattr(self, "base_output_dir", None),
            )
            self.evidence_registry = registry
        return registry

    def _get_report_service(self) -> ReportService:
        service = getattr(self, "report_service", None)
        is_injected = getattr(
            self,
            "_report_service_is_injected",
            service is not None,
        )
        registry = self._get_evidence_registry()
        if service is None or (
            not is_injected
            and getattr(service, "evidence_registry", None) is not registry
        ):
            renderers = default_renderers()
            renderers[ReportFormat.DOCX] = WordReportRenderer(
                generator=generate_word_report
            )
            service = ReportService(
                renderers=renderers,
                artifact_storage=getattr(self, "artifact_storage", None),
                storage=getattr(self, "storage", None),
                evidence_registry=registry,
                allowed_output_root=(
                    getattr(self, "session_output_dir", None)
                    or getattr(self, "base_output_dir", None)
                ),
            )
            self.report_service = service
        return service

    def register_metric(self, metric: MetricArtifact) -> MetricArtifact:
        """Register a structured metric without inferring values from stdout."""
        if metric.task_id is None:
            metric = metric.model_copy(update={"task_id": self.task_id})
        return self._get_evidence_registry().register_metric(metric)

    def validate_claim(self, claim: EvidenceClaim) -> EvidenceClaim:
        return self._get_evidence_registry().validate_claim(claim)

    def _request_structured_action(
        self, prompt: str, system_prompt: str | None = None
    ) -> AgentAction:
        return self._get_llm_port().request_action(prompt, system_prompt)

    @staticmethod
    def _action_payload(action: AgentAction) -> Dict[str, Any]:
        return action.model_dump(mode="python")

    def _process_action(
        self, action: AgentAction, response: str = ""
    ) -> Dict[str, Any]:
        payload = self._action_payload(action)
        if action.action == "analysis_complete":
            return self._handle_analysis_complete(response, payload)
        if action.action == "collect_figures":
            return self._handle_collect_figures(response, payload)
        return self._handle_generate_code(response, payload)

    def _process_response(self, response: str) -> Dict[str, Any]:
        """
        统一处理LLM响应，判断行动类型并执行相应操作

        Args:
            response: LLM的响应内容

        Returns:
            处理结果字典
        """
        try:
            yaml_data = self.llm.parse_yaml_response(response)
            action = yaml_data.get('action', 'generate_code')

            print(f"🎯 检测到动作: {action}")

            if action == 'analysis_complete':
                return self._handle_analysis_complete(response, yaml_data)
            elif action == 'collect_figures':
                return self._handle_collect_figures(response, yaml_data)
            elif action == 'generate_code':
                return self._handle_generate_code(response, yaml_data)
            else:
                print(f"⚠️ 未知动作类型: {action}，按generate_code处理")
                return self._handle_generate_code(response, yaml_data)

        except Exception as e:
            print(
                "⚠️ 解析响应失败: "
                + sanitize_exception(
                    e,
                    secrets=(
                        getattr(self.config, "api_key", None),
                        getattr(self.config, "base_url", None),
                    ),
                    include_message=False,
                )
                + "，按generate_code处理"
            )
            return self._handle_generate_code(response, {})

    def _handle_analysis_complete(self, response: str, yaml_data: Dict[str, Any]) -> Dict[str, Any]:
        """处理分析完成动作"""
        print("✅ 分析任务完成")
        final_report = yaml_data.get('final_report', '分析完成，无最终报告')
        return {
            'action': 'analysis_complete',
            'final_report': final_report,
            'response': response,
            'continue': False
        }

    def _handle_collect_figures(self, response: str, yaml_data: Dict[str, Any]) -> Dict[str, Any]:
        """处理图片收集动作"""
        print("📊 开始收集图片")
        figures_to_collect = yaml_data.get('figures_to_collect', [])

        collected_figures = []

        for figure_info in figures_to_collect:
            figure_number = figure_info.get('figure_number')
            filename = figure_info.get('filename', f'figure_{figure_number}.png')
            file_path = figure_info.get('file_path', '')  # 获取具体的文件路径
            if file_path:
                file_path = self._resolve_executor_path(file_path)
                if not file_path:
                    print("   ⚠️ 图片路径不在执行输出目录内，已忽略")
                    continue
            description = figure_info.get('description', '')
            analysis = figure_info.get('analysis', '')
            print(f"📈 收集图片 {figure_number}: {filename}")
            print(f"   📂 路径: {self._display_text(file_path)}")
            print(f"   📝 描述: {description}")
            print(f"   🔍 分析: {analysis}")

            # 验证文件是否存在
            if file_path and os.path.exists(file_path):
                print(f"   ✅ 文件存在: {self._display_text(file_path)}")
            elif file_path:
                print(f"   ⚠️ 文件不存在: {self._display_text(file_path)}")
            else:
                print(f"   ⚠️ 未提供文件路径")

            evidence_verified = False
            if file_path:
                try:
                    chart = ChartArtifact(
                        task_id=self.task_id,
                        filename=str(filename),
                        file_path=str(file_path),
                        title=str(figure_info.get("title") or filename),
                        description=str(description) if description else None,
                        chart_type=(
                            str(figure_info["chart_type"])
                            if figure_info.get("chart_type")
                            else None
                        ),
                        execution_id=getattr(self, "_last_execution_id", None),
                        code_hash=getattr(self, "_last_code_hash", None),
                    )
                    registered_chart = self._get_evidence_registry().register_chart(chart)
                    self._get_evidence_registry().check_chart(registered_chart.artifact_id)
                    evidence_verified = True
                except EvidenceError:
                    # Preserve the legacy collection payload for storage/error
                    # handling, while excluding the chart from report evidence.
                    evidence_verified = False

            # 记录图片信息
            collected_figures.append({
                'figure_number': figure_number,
                'filename': filename,
                'file_path': file_path,
                'description': description,
                'analysis': analysis,
                'evidence_verified': evidence_verified,
            })

        return {
            'action': 'collect_figures',
            'collected_figures': collected_figures,
            'response': response,
            'continue': True
        }
    def _handle_generate_code(self, response: str, yaml_data: Dict[str, Any]) -> Dict[str, Any]:
        """处理代码生成和执行动作"""
        # 从YAML数据中获取代码（更准确）
        code = yaml_data.get('code', '')

        # 如果YAML中没有代码，尝试从响应中提取
        if not code:
            code = extract_code_from_response(response)

        if code:
            original_code = code
            code = self._display_text(code)
            print(f"🔧 执行代码:\n{code}")
            print("-" * 40)
            code = original_code

            # 执行代码
            result = self.executor.execute_code(code)
            if isinstance(result, Mapping):
                audit = result.get("audit")
                if isinstance(audit, Mapping):
                    self.execution_audits.append(dict(audit))
                    execution_id = audit.get("execution_id")
                    if execution_id:
                        try:
                            self._last_execution_id = UUID(str(execution_id))
                        except (TypeError, ValueError):
                            self._last_execution_id = None
                    self._last_code_hash = audit.get("code_sha256")

            # 格式化执行结果
            feedback = format_execution_result(result)
            original_feedback = feedback
            feedback = self._display_text(feedback)
            print(f"📋 执行反馈:\n{feedback}")
            feedback = original_feedback

            return {
                'action': 'generate_code',
                'code': code,
                'result': result,
                'feedback': feedback,
                'response': response,
                'continue': True
            }
        else:
            # 如果没有代码，说明LLM响应格式有问题，需要重新生成
            print("⚠️ 未从响应中提取到可执行代码，要求LLM重新生成")
            return {
                'action': 'invalid_response',
                'error': '响应中缺少可执行代码',
                'response': response,
                'continue': True
            }

    def analyze(
        self,
        user_input: str,
        files: Sequence[str] | None = None,
        *,
        dataset_ids: Sequence[UUID | str] | None = None,
    ) -> Dict[str, Any]:
        depth = getattr(self, "_analysis_depth", 0)
        self._analysis_depth = depth + 1
        try:
            return self._analyze_impl(
                user_input,
                files,
                dataset_ids=dataset_ids,
            )
        finally:
            self._analysis_depth = depth
            if depth == 0:
                self._close_execution_session()
                self._close_owned_llm()

    def _analyze_impl(
        self,
        user_input: str,
        files: Sequence[str] | None = None,
        *,
        dataset_ids: Sequence[UUID | str] | None = None,
    ) -> Dict[str, Any]:
        """
        开始分析流程

        Args:
            user_input: 用户的自然语言需求
            files: 兼容旧调用的本地文件；仅在本次分析期间临时存储
            dataset_ids: 通过 DatasetResolver 访问的数据集 ID 列表

        Returns:
            分析结果字典
        """
        # 重置状态
        self.conversation_history = []
        self.analysis_results = []
        self.current_round = 0
        self.task_id = self._provided_task_id or uuid4()
        provided_registry = getattr(self, "_provided_evidence_registry", None)
        if (
            provided_registry is not None
            and provided_registry.task_id == self.task_id
        ):
            self.evidence_registry = provided_registry
        else:
            self.evidence_registry = EvidenceRegistry(
                task_id=self.task_id,
                output_root=self.base_output_dir,
            )
        self.artifact_records = []
        self.execution_audits = []
        self._last_execution_id = None
        self._last_code_hash = None
        self.storage_error = None

        if files is not None and dataset_ids is not None:
            raise UploadValidationError(
                DatasetErrorCode.INVALID_DATASET_REQUEST,
                "files and dataset_ids cannot be supplied together",
            )
        if files is not None:
            with _upload_compatibility_files(
                files, settings=self.settings
            ) as (uploaded_ids, resolver, owner_id):
                previous_resolver, previous_owner = self.dataset_resolver, self.dataset_owner_id
                try:
                    self.dataset_resolver = resolver
                    self.dataset_owner_id = owner_id
                    return self.analyze(user_input, dataset_ids=uploaded_ids)
                finally:
                    self.dataset_resolver = previous_resolver
                    self.dataset_owner_id = previous_owner

        normalized_dataset_ids = tuple(
            UUID(str(dataset_id)) for dataset_id in (dataset_ids or ())
        )
        dataset_context = []
        profiles_by_id = {}
        if normalized_dataset_ids:
            if self.dataset_resolver is None:
                raise DatasetAccessDeniedError(
                    DatasetErrorCode.DATASET_ACCESS_DENIED,
                    "dataset_resolver is required for dataset_ids",
                )
            if self.dataset_owner_id is None:
                raise DatasetAccessDeniedError(
                    DatasetErrorCode.DATASET_ACCESS_DENIED,
                    "dataset_owner_id is required for dataset_ids",
                )
            for dataset_id in normalized_dataset_ids:
                profile = self.dataset_resolver.profile_for_user(
                    dataset_id, owner_id=self.dataset_owner_id
                )
                dataset_context.append(
                    {
                        "dataset_id": str(dataset_id),
                        "profile": profile.model_dump(mode="json"),
                    }
                )
                profiles_by_id[dataset_id] = profile

        # 创建本次分析的专用输出目录
        try:
            self.session_output_dir = create_session_output_dir(
                self.base_output_dir, user_input
            )
        except Exception:
            self.cleanup_storage_outputs()
            raise

        # 初始化代码执行器，使用会话目录
        try:
            self.executor = self._build_execution_session()
        except Exception:
            self.cleanup_storage_outputs()
            raise
        if dataset_context:
            self.executor.set_sensitive_columns(
                field["column_name"]
                for item in dataset_context
                for field in item["profile"].get("sensitive_fields", ())
            )

        # 设置会话目录变量到执行环境中
        self.executor.set_variable('session_output_dir', self.session_output_dir)
        if normalized_dataset_ids:
            def load_dataset(dataset_id: str):
                normalized_id = UUID(str(dataset_id))
                profile = profiles_by_id[normalized_id]
                profile_payload = profile.model_dump(mode="json")
                encoding = getattr(profile, "encoding", profile_payload.get("encoding", "utf-8"))
                delimiter = getattr(profile, "delimiter", profile_payload.get("delimiter", ","))
                with self.dataset_resolver.open_for_user(
                    normalized_id, owner_id=self.dataset_owner_id
                ) as stream:
                    dataframe = pandas.read_csv(
                        stream,
                        encoding=encoding,
                        sep=delimiter,
                    )
                profile_columns = [
                    column["name"]
                    for column in profile_payload.get("columns", ())
                    if isinstance(column, dict) and "name" in column
                ]
                if len(profile_columns) == len(dataframe.columns):
                    dataframe.columns = profile_columns
                else:
                    dataframe.columns = [str(column).strip() for column in dataframe.columns]
                return dataframe

            self.executor.set_variable("load_dataset", load_dataset)
            self.executor.set_variable(
                "dataset_ids", tuple(str(item) for item in normalized_dataset_ids)
            )

        # 构建初始prompt
        initial_prompt = f"""用户需求: {user_input}"""
        if dataset_context:
            initial_prompt += "\n可用数据集:\n"
            initial_prompt += "\n".join(
                "dataset_id={dataset_id} profile={profile}".format(
                    dataset_id=item["dataset_id"],
                    profile=json.dumps(item["profile"], ensure_ascii=False, sort_keys=True),
                )
                for item in dataset_context
            )

        print(f"🚀 开始数据分析任务")
        print(f"📝 用户需求: {user_input}")
        if dataset_context:
            print(f"📊 可用数据集: {len(dataset_context)} 个")
        print(f"📂 输出目录: {self._display_text(self.session_output_dir)}")
        print(f"🔢 最大轮数: {self.max_rounds}")
        print("=" * 60)
        # 添加到对话历史
        self.conversation_history.append({
            'role': 'user',
            'content': initial_prompt
        })

        adapter = LegacyAnalysisAdapter(
            agent=self,
            user_input=user_input,
            dataset_context=dataset_context,
            max_rounds=self.max_rounds,
        )
        self.orchestrator = AgentOrchestrator(
            task=adapter.task,
            handlers=adapter.handlers(),
            limits=adapter.limits,
            tool_executor=self.tool_executor,
            tool_context_factory=lambda task_id: ToolContext(
                task_id=task_id,
                user_id=self.dataset_owner_id,
            ),
        )
        orchestration_result = self.orchestrator.run()
        if orchestration_result.status is not TaskStatus.COMPLETED:
            report_exception = getattr(adapter, "report_exception", None)
            if report_exception is not None:
                self.cleanup_storage_outputs()
                self._raise_compatibility_exception(report_exception)

            failure_code = None
            for event in reversed(orchestration_result.state.events):
                if getattr(event.event_type, "value", event.event_type) != "ERROR":
                    continue
                failure_code = event.metadata.get("cause_code")
                break

            # The original DataAnalysisAgent continued to the final report
            # after a transient gateway failure.  Preserve that public
            # behavior while leaving the orchestrator's failed state and
            # sanitized error event intact.
            if failure_code == "MODEL_ERROR":
                compatibility_error = f"LLM调用错误: {failure_code}"
                print(f"❌ {compatibility_error}")
                self.conversation_history.append(
                    {"role": "user", "content": compatibility_error}
                )
                try:
                    fallback_report = self._generate_final_report()
                except Exception as exc:
                    self.cleanup_storage_outputs()
                    self._raise_compatibility_exception(exc)
                if isinstance(fallback_report, Mapping):
                    fallback_result = orchestration_result.model_copy(
                        update={
                            "output": LegacyAnalysisAdapter._json_safe(
                                fallback_report
                            )
                        }
                    )
                    return adapter.to_legacy_result(
                        fallback_result,
                        json_safe=False,
                    )

            self.cleanup_storage_outputs()
            raise AgentOrchestrationError(
                orchestration_result.error_message or "analysis orchestration failed",
                cause_code=orchestration_result.error_code or "ORCHESTRATOR_FAILED",
            )
        return adapter.to_legacy_result(orchestration_result, json_safe=False)

    def _raise_compatibility_exception(self, exception: BaseException) -> None:
        """Re-raise legacy failures with provider/path details redacted."""
        safe_message = sanitize_exception(
            exception,
            secrets=(
                getattr(self.config, "api_key", None),
                getattr(self.config, "base_url", None),
            ),
        )
        try:
            safe_exception = type(exception)(safe_message)
        except Exception:
            safe_exception = RuntimeError(safe_message)
        raise safe_exception from None

    def cleanup_storage_outputs(self) -> None:
        """Remove storage-backed artifacts and the private staging directory."""
        artifact_storage = getattr(self, "artifact_storage", None)
        artifact_records = getattr(self, "artifact_records", [])
        task_id = getattr(self, "task_id", None)
        lifecycle = getattr(self, "storage_lifecycle", None)
        if lifecycle is not None:
            try:
                lifecycle.cleanup_failed_task(
                    task_id=task_id,
                    staging_dir=(
                        Path(self.session_output_dir)
                        if getattr(self, "session_output_dir", None)
                        else None
                    ),
                    records=tuple(artifact_records),
                )
            except Exception:
                logging.getLogger(__name__).warning(
                    "Storage-backed artifact cleanup failed"
                )
            return
        if artifact_storage is not None and artifact_records and task_id:
            try:
                artifact_storage.delete_task_files(
                    task_id=task_id,
                    records=tuple(artifact_records),
                )
            except Exception:
                logging.getLogger(__name__).warning(
                    "Storage-backed artifact cleanup failed"
                )

        if not self.session_output_dir:
            return
        try:
            output_root = Path(self.base_output_dir).resolve(strict=False)
            staging_dir = Path(self.session_output_dir).resolve(strict=False)
            if staging_dir == output_root:
                return
            staging_dir.relative_to(output_root)
            if staging_dir.exists():
                shutil.rmtree(staging_dir)
        except (OSError, RuntimeError, ValueError):
            logging.getLogger(__name__).warning(
                "Analysis staging cleanup failed"
            )

    def _staged_file(self, file_path: object) -> Path:
        if not self.session_output_dir:
            raise StorageError(
                StorageErrorCode.INVALID_URI,
                "analysis staging directory is unavailable",
            )
        candidate = Path(str(file_path))
        if not candidate.is_absolute():
            candidate = Path(self.session_output_dir) / candidate
        try:
            resolved = candidate.resolve(strict=False)
            root = Path(self.session_output_dir).resolve(strict=False)
            resolved.relative_to(root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise StorageError(
                StorageErrorCode.INVALID_URI,
                "artifact source is outside the analysis staging directory",
            ) from exc
        if not resolved.is_file():
            raise StorageError(
                StorageErrorCode.OBJECT_NOT_FOUND,
                "artifact source is not available",
            )
        return resolved

    def _store_figure_artifacts(self, figures: list[dict[str, Any]]) -> None:
        artifact_storage = getattr(self, "artifact_storage", None)
        task_id = getattr(self, "task_id", None)
        if artifact_storage is None or task_id is None:
            return
        mime_types = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".svg": "image/svg+xml",
            ".webp": "image/webp",
        }
        for figure in figures:
            source_path = self._staged_file(figure.get("file_path", ""))
            filename = figure.get("filename") or source_path.name
            record = artifact_storage.store_file(
                task_id=task_id,
                source_path=source_path,
                artifact_type="CHART",
                filename=str(filename),
                mime_type=mime_types.get(source_path.suffix.lower(), "image/png"),
                description=figure.get("description") or None,
            )
            self.artifact_records.append(record)

    def _store_report_artifact(
        self,
        source_path: str,
        *,
        filename: str,
        mime_type: str,
        format: str,
    ) -> str | None:
        artifact_storage = getattr(self, "artifact_storage", None)
        task_id = getattr(self, "task_id", None)
        storage = getattr(self, "storage", None)
        if artifact_storage is None or task_id is None or storage is None:
            return None
        artifact, _report = artifact_storage.store_report(
            task_id=task_id,
            source_path=self._staged_file(source_path),
            filename=filename,
            mime_type=mime_type,
            format=format,
        )
        self.artifact_records.append(artifact)
        if not artifact.file_path:
            return None
        return storage.create_download_url(artifact.file_path)

    def _record_storage_error(self, exc: BaseException) -> None:
        error = sanitize_exception(
            exc,
            secrets=(
                getattr(self.config, "api_key", None),
                getattr(self.config, "base_url", None),
            ),
        )
        self.storage_error = self._display_text(error)
        print(f"❌ 文件存储失败: {self.storage_error}")

    def _report_result_download_url(self, result: object | None) -> str | None:
        if result is None:
            return None
        download_url = getattr(result, "download_url", None)
        if download_url:
            return str(download_url)
        artifact = getattr(result, "artifact", None)
        storage_uri = getattr(artifact, "file_path", None)
        storage = getattr(self, "storage", None)
        if not storage_uri or storage is None:
            return None
        try:
            return storage.create_download_url(storage_uri)
        except Exception as exc:
            self._record_storage_error(exc)
            return None

    @staticmethod
    def _report_result_payload(result: object) -> dict[str, Any]:
        model_dump = getattr(result, "model_dump", None)
        if callable(model_dump):
            return model_dump(mode="json")
        artifact = getattr(result, "artifact", None)
        artifact_dump = getattr(artifact, "model_dump", None)
        return {
            "format": getattr(getattr(result, "format", None), "value", None),
            "generated": bool(getattr(result, "generated", False)),
            "file_path": getattr(result, "file_path", None),
            "download_url": getattr(result, "download_url", None),
            "artifact": (
                artifact_dump(mode="json") if callable(artifact_dump) else artifact
            ),
            "error": getattr(result, "error", None),
        }

    def _append_report_artifacts(self, results: Sequence[object]) -> list[object]:
        records = getattr(self, "artifact_records", None)
        if records is None:
            records = []
            self.artifact_records = records
        existing_ids = {
            getattr(record, "artifact_id", None)
            for record in records
        }
        report_artifacts: list[object] = []
        mime_types = {
            ReportFormat.MARKDOWN: "text/markdown; charset=utf-8",
            ReportFormat.HTML: "text/html; charset=utf-8",
            ReportFormat.DOCX: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }
        for result in results:
            artifact = getattr(result, "artifact", None)
            if not getattr(result, "generated", False) or artifact is None:
                continue
            report_artifacts.append(artifact)
            artifact_id = getattr(artifact, "artifact_id", None)
            if artifact_id in existing_ids:
                continue
            if isinstance(artifact, ArtifactRecord):
                record = artifact
            else:
                report_format = getattr(result, "format", getattr(artifact, "format", None))
                local_path = getattr(result, "file_path", None)
                record = ArtifactRecord(
                    artifact_id=artifact_id,
                    task_id=self.task_id,
                    artifact_type="REPORT",
                    name=(
                        Path(local_path).name
                        if local_path
                        else f"report.{getattr(report_format, 'value', 'report').lower()}"
                    ),
                    file_path=getattr(artifact, "file_path", None),
                    format=getattr(report_format, "value", str(report_format)),
                    mime_type=mime_types.get(report_format),
                    content_hash=getattr(artifact, "content_hash", None),
                    created_at=getattr(artifact, "created_at"),
                    size_bytes=getattr(artifact, "size_bytes", 0),
                    title=getattr(artifact, "title", None),
                    metadata_json=dict(getattr(artifact, "metadata", {})),
                )
            records.append(record)
            existing_ids.add(artifact_id)
        return report_artifacts

    def _display_text(self, value: object) -> str:
        """Redact private staging paths from storage-mode console output."""
        text = str(value)
        if getattr(self, "storage", None) is None:
            return text
        staging_dir = getattr(self, "session_output_dir", None)
        if not staging_dir:
            return text
        candidates = {str(staging_dir)}
        try:
            candidates.add(str(Path(staging_dir).resolve(strict=False)))
        except (OSError, RuntimeError, TypeError, ValueError):
            pass
        for candidate in sorted(candidates, key=len, reverse=True):
            if candidate:
                text = text.replace(candidate, "[private staging path]")
        if text == str(value):
            candidate_path = None
            try:
                candidate_path = Path(text)
                candidate_path.resolve(strict=False).relative_to(
                    Path(staging_dir).resolve(strict=False)
                )
            except (OSError, RuntimeError, TypeError, ValueError):
                if candidate_path is not None and candidate_path.is_absolute():
                    return "[external path redacted]"
        text = re.sub(
            r"(?<![\w])(?:[A-Za-z]:[\\/]+)(?:[^\\/\s'\"<>()\[\]{},;]+[\\/]+)*[^\\/\s'\"<>()\[\]{},;]+",
            "[external path redacted]",
            text,
        )
        text = re.sub(
            r"(?<![\w:])/(?!/)(?:[^/\s'\"<>()\[\]{},;]+/)*[^/\s'\"<>()\[\]{},;]+",
            "[external path redacted]",
            text,
        )
        return text

    def _resolve_executor_path(self, value: object) -> str:
        """Map a fixed executor path such as ``/output/chart.png`` to staging."""
        resolver = getattr(getattr(self, "executor", None), "resolve_output_path", None)
        if not callable(resolver):
            return str(value)
        try:
            return str(resolver(value))
        except (OSError, RuntimeError, ValueError):
            return ""

    def _prompt_output_dir(self) -> str:
        if isinstance(getattr(self, "executor", None), AgentExecutionSession):
            return "/output"
        return str(self.session_output_dir)

    def _build_conversation_prompt(self) -> str:
        """构建对话提示词"""
        prompt_parts = []

        for msg in self.conversation_history:
            role = msg['role']
            content = msg['content']
            if role == 'user':
                prompt_parts.append(f"用户: {content}")
            else:
                prompt_parts.append(f"助手: {content}")

        return "\n\n".join(prompt_parts)

    def _generate_final_report(self) -> Dict[str, Any]:
        """生成最终分析报告"""
        # 收集所有生成的图片信息
        all_figures = []
        for result in self.analysis_results:
            if result.get('action') == 'collect_figures':
                all_figures.extend(result.get('collected_figures', []))

        print(f"\n📊 开始生成最终分析报告...")
        print(f"📂 输出目录: {self._display_text(self.session_output_dir)}")
        print(f"🔢 总轮数: {self.current_round}")
        print(f"📈 收集图片: {len(all_figures)} 个")

        # 构建用于生成最终报告的提示词
        final_report_prompt = self._build_final_report_prompt(all_figures)
        structured_report_error: Exception | None = None

        try:            # 调用LLM生成最终报告
            final_report_content = self._get_llm_port().request_report(
                prompt=final_report_prompt,
                system_prompt="你将会接收到一个数据分析任务的最终报告请求，请根据提供的分析结果和图片信息生成完整的分析报告。",
            )

            print("✅ 最终报告生成完成")

        except (LLMStructuredOutputError, ValidationError) as e:
            structured_report_error = e
            safe_error = sanitize_exception(
                e,
                secrets=(
                    getattr(self.config, "api_key", None),
                    getattr(self.config, "base_url", None),
                ),
                include_message=False,
            )
            print(f"❌ 生成最终报告时出错: {self._display_text(safe_error)}")
            final_report_content = f"报告生成失败: {safe_error}"
        except Exception as e:
            safe_error = sanitize_exception(
                e,
                secrets=(
                    getattr(self.config, "api_key", None),
                    getattr(self.config, "base_url", None),
                ),
                include_message=False,
            )
            print(f"❌ 生成最终报告时出错: {self._display_text(safe_error)}")
            final_report_content = f"报告生成失败: {safe_error}"

        evidence_registry = self._get_evidence_registry()
        evidence_snapshot = evidence_registry.snapshot(self.task_id)
        document = ReportDocument(
            task_id=self.task_id,
            output_root=str(self.session_output_dir),
            narrative_markdown=final_report_content,
            metric_artifacts=tuple(evidence_snapshot["metrics"]),
            chart_artifacts=tuple(evidence_snapshot["charts"]),
            evidence_validation=evidence_snapshot.get("validation"),
        )
        formats = {ReportFormat.MARKDOWN, ReportFormat.HTML}
        if self.generate_word_report:
            formats.add(ReportFormat.DOCX)

        if getattr(self, "artifact_storage", None) is not None:
            try:
                self._store_figure_artifacts(all_figures)
            except Exception as exc:
                self._record_storage_error(exc)

        bundle = self._get_report_service().generate(document, formats=formats)
        results = tuple(bundle.results)
        results_by_format = {result.format: result for result in results}
        markdown_result = results_by_format.get(ReportFormat.MARKDOWN)
        html_result = results_by_format.get(ReportFormat.HTML)
        word_result = results_by_format.get(ReportFormat.DOCX)

        final_report_content = bundle.markdown_content
        report_file_path = getattr(markdown_result, "file_path", None)
        report_download_url = self._report_result_download_url(markdown_result)
        html_report_file_path = getattr(html_result, "file_path", None)
        html_report_generated = bool(
            getattr(html_result, "generated", False)
        )
        html_report_error = getattr(html_result, "error", None)
        html_report_download_url = self._report_result_download_url(html_result)

        word_report_generated = bool(
            getattr(word_result, "generated", False)
        )
        word_report_error = getattr(word_result, "error", None)
        word_report_file_path = getattr(word_result, "file_path", None)
        if self.generate_word_report and not word_report_file_path:
            word_report_file_path = str(
                Path(self.session_output_dir) / "最终分析报告.docx"
            )
        word_report_download_url = self._report_result_download_url(word_result)

        if report_file_path:
            print(f"📄 最终报告已保存至: {self._display_text(report_file_path)}")
        if html_report_file_path:
            print(f"📄 HTML报告已保存至: {self._display_text(html_report_file_path)}")
        if word_report_generated and word_report_file_path:
            print(f"📄 Word报告已保存至: {self._display_text(word_report_file_path)}")
        elif word_report_error:
            print(f"❌ Word报告生成失败: {self._display_text(word_report_error)}")

        storage_errors = tuple(getattr(bundle, "storage_errors", ()))
        if storage_errors:
            prior_error = getattr(self, "storage_error", None)
            combined = "; ".join(str(error) for error in storage_errors)
            self.storage_error = (
                f"{prior_error}; {combined}" if prior_error else combined
            )
            print(f"❌ 文件存储失败: {self._display_text(combined)}")

        report_artifact_models = self._append_report_artifacts(results)

        if structured_report_error is not None:
            raise structured_report_error

        evidence_snapshot = evidence_registry.snapshot(self.task_id)
        evidence_validation = bundle.evidence_validation

        # 返回完整的分析结果
        return {
            'session_output_dir': self.session_output_dir,
            'total_rounds': self.current_round,
            'analysis_results': self.analysis_results,
            'collected_figures': all_figures,
            'conversation_history': self.conversation_history,
            'final_report': final_report_content,
            'report_file_path': report_file_path,
            'html_report_file_path': html_report_file_path,
            'html_report_generated': html_report_generated,
            'html_report_error': html_report_error,
            'word_report_file_path': word_report_file_path,
            'word_report_generated': word_report_generated,
            'word_report_error': word_report_error,
            'task_id': getattr(self, "task_id", None),
            'artifact_records': list(getattr(self, "artifact_records", [])),
            'report_download_url': report_download_url,
            'html_report_download_url': html_report_download_url,
            'word_report_download_url': word_report_download_url,
            'report_results': [
                self._report_result_payload(result) for result in results
            ],
            'report_artifacts': [
                artifact.model_dump(mode="json")
                if callable(getattr(artifact, "model_dump", None))
                else artifact
                for artifact in report_artifact_models
            ],
            'storage_error': getattr(self, "storage_error", None),
            'metric_artifacts': [
                item.model_dump(mode="json")
                for item in evidence_snapshot["metrics"]
            ],
            'chart_artifacts': [
                item.model_dump(mode="json")
                for item in evidence_snapshot["charts"]
            ],
            'evidence_claims': [
                item.model_dump(mode="json")
                for item in evidence_snapshot["claims"]
            ],
            'evidence_validation': evidence_validation.model_dump(mode="json"),
        }

    def _build_final_report_prompt(self, all_figures: List[Dict[str, Any]]) -> str:
        """构建用于生成最终报告的提示词"""

        report_figures = [
            figure
            for figure in all_figures
            if figure.get("evidence_verified", True)
        ]

        # 构建图片信息摘要，使用相对路径
        figures_summary = ""
        if report_figures:
            figures_summary = "\n生成的图片及分析:\n"
            for i, figure in enumerate(report_figures, 1):
                filename = figure.get('filename', '未知文件名')
                # 使用相对路径格式，适合在报告中引用
                relative_path = f"./{filename}"
                figures_summary += f"{i}. {filename}\n"
                figures_summary += f"   相对路径: {relative_path}\n"
                figures_summary += f"   描述: {figure.get('description', '无描述')}\n"
                figures_summary += f"   分析: {figure.get('analysis', '无分析')}\n\n"
        else:
            figures_summary = "\n本次分析未生成图片。\n"

        # 构建代码执行结果摘要（仅包含成功执行的代码块）
        code_results_summary = ""
        success_code_count = 0
        for result in self.analysis_results:
            if result.get('action') != 'collect_figures' and result.get('code'):
                exec_result = result.get('result', {})
                if exec_result.get('success'):
                    success_code_count += 1
                    code_results_summary += f"代码块 {success_code_count}: 执行成功\n"
                    if exec_result.get('output'):
                        code_results_summary += f"输出: {exec_result.get('output')[:]}\n\n"


        # 使用 prompts.py 中的统一提示词模板，并添加相对路径使用说明
        prompt = final_report_system_prompt.format(
            current_round=self.current_round,
            session_output_dir=self._prompt_output_dir(),
            figures_summary=figures_summary,
            code_results_summary=code_results_summary
        )

        # 在提示词中明确要求使用相对路径
        prompt += """

📁 **图片路径使用说明**：
报告和图片都在同一目录下，请在报告中使用相对路径引用图片：
- 格式：![图片描述](./图片文件名.png)
- 示例：![营业收入趋势](./营业收入趋势.png)
- 这样可以确保报告在不同环境下都能正确显示图片
"""

        prompt += self._build_verified_evidence_context()

        return prompt

    def _build_verified_evidence_context(self) -> str:
        """Return only provenance-backed evidence for the report model."""
        snapshot = self._get_evidence_registry().snapshot(self.task_id)
        lines = [
            "\n结构化证据（只能引用以下已验证事实；未列出的数字不得写入报告）:",
        ]
        for metric in snapshot["metrics"]:
            if metric.verification_status is not EvidenceVerificationStatus.VERIFIED:
                continue
            datasets = ",".join(str(item) for item in metric.source_dataset_ids)
            lines.append(
                "- metric_id={metric_id} name={name} value={value} unit={unit} "
                "formula={formula} datasets={datasets} execution_id={execution_id} "
                "code_hash={code_hash} status=VERIFIED".format(
                    metric_id=metric.artifact_id,
                    name=metric.name,
                    value=metric.value,
                    unit=metric.unit or "",
                    formula=metric.formula or "",
                    datasets=datasets,
                    execution_id=metric.execution_id or "",
                    code_hash=metric.code_hash or "",
                )
            )
        for chart in snapshot["charts"]:
            if chart.verification_status is not EvidenceVerificationStatus.VERIFIED:
                continue
            lines.append(
                "- chart_id={chart_id} filename={filename} chart_type={chart_type} "
                "status=VERIFIED".format(
                    chart_id=chart.artifact_id,
                    filename=chart.filename,
                    chart_type=chart.chart_type or "",
                )
            )
        return "\n".join(lines) + "\n"

    def reset(self):
        """重置智能体状态"""
        self.conversation_history = []
        self.analysis_results = []
        self.current_round = 0
        self.executor.reset_environment()


def quick_analysis(
    query: str,
    files: Sequence[str] | None = None,
    *,
    dataset_ids: Sequence[UUID | str] | None = None,
    output_dir: str | Path | None = None,
    max_rounds: int | None = None,
    generate_word_report: bool | None = None,
    settings: Settings | None = None,
    dataset_resolver: DatasetResolver | None = None,
    dataset_owner_id: UUID | None = None,
    storage: Storage | None = None,
) -> dict[str, Any]:
    resolved_settings = settings or load_settings()
    if files is not None and dataset_ids is not None:
        raise UploadValidationError(
            DatasetErrorCode.INVALID_DATASET_REQUEST,
            "files and dataset_ids cannot be supplied together",
        )

    selected_resolver = dataset_resolver
    selected_owner_id = dataset_owner_id
    selected_dataset_ids = dataset_ids
    if dataset_ids:
        missing = []
        if dataset_resolver is None:
            missing.append("dataset_resolver")
        if dataset_owner_id is None:
            missing.append("dataset_owner_id")
        if missing:
            raise DatasetAccessDeniedError(
                DatasetErrorCode.DATASET_ACCESS_DENIED,
                "missing dataset access dependency: " + ", ".join(missing),
            )

    with ExitStack() as uploads:
        if files is not None:
            (
                selected_dataset_ids,
                selected_resolver,
                selected_owner_id,
            ) = uploads.enter_context(
                _upload_compatibility_files(files, settings=resolved_settings)
            )

        selected_output_dir = (
            output_dir
            if output_dir is not None and str(output_dir).strip()
            else resolved_settings.output_dir
        )
        selected_storage = storage
        if selected_storage is None and getattr(
            resolved_settings, "storage_local_root", None
        ) is not None:
            selected_storage = build_storage(resolved_settings)
        agent = DataAnalysisAgent(
            llm_config=resolved_settings.llm_config(),
            output_dir=str(selected_output_dir),
            max_rounds=max_rounds if max_rounds is not None else 10,
            generate_word_report=(
                generate_word_report
                if generate_word_report is not None
                else resolved_settings.app_env != "test"
            ),
            dataset_resolver=selected_resolver,
            dataset_owner_id=selected_owner_id,
            storage=selected_storage,
            settings=resolved_settings,
        )
        try:
            return agent.analyze(
                user_input=query,
                dataset_ids=selected_dataset_ids,
            )
        except Exception:
            cleanup = getattr(agent, "cleanup_storage_outputs", None)
            if cleanup is not None:
                cleanup()
            raise
