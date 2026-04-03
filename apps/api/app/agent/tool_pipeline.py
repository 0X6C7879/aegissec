from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from app.agent.tool_registry import (
    ToolExecutionHooks,
    ToolExecutionPolicy,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolPolicyDecision,
    ToolSpec,
    build_tool_input_payload,
)
from app.agent.tool_runtime_models import (
    ToolExecutionEnvelope,
    ToolExecutionError,
    ToolRuntimeResult,
    ToolValidationError,
)
from app.agent.tool_transcript import build_tool_transcript_blocks
from app.db.models import TaskNodeStatus


@dataclass(frozen=True)
class ToolPipelineRegisteredTool:
    spec: ToolSpec
    handler: Any


class ToolPipeline:
    def __init__(self, *, policy: ToolExecutionPolicy, hooks: ToolExecutionHooks) -> None:
        self._policy = policy
        self._hooks = hooks

    def execute(
        self,
        *,
        request: ToolExecutionRequest,
        registered_tool: ToolPipelineRegisteredTool,
    ) -> ToolExecutionEnvelope:
        spec = registered_tool.spec
        pipeline_stages = [
            "resolve",
            "schema_validate",
            "semantic_validate",
            "policy",
            "pre_hooks",
            "execute",
            "post_hooks",
            "standardize_result",
            "generate_transcript_blocks",
        ]
        normalized_input = spec.normalize_input(build_tool_input_payload(request))
        try:
            self._validate_schema(
                schema=spec.input_schema,
                payload=normalized_input,
                stage="schema_validate",
                label="input",
            )
            spec.validate_input(normalized_input, request=request)
        except ToolValidationError as error:
            return self._build_validation_failure_envelope(
                request=request,
                spec=spec,
                input_payload=normalized_input,
                error=error,
                pipeline_stages=pipeline_stages,
            )

        policy_decision = self._policy.evaluate(request=request, spec=spec)
        if not policy_decision.allowed:
            return self._build_policy_denied_envelope(
                request=request,
                spec=spec,
                input_payload=normalized_input,
                decision=policy_decision,
                pipeline_stages=pipeline_stages,
            )

        if spec.requires_user_interaction() or spec.interrupt_behavior().value != "none":
            return self._build_interaction_required_envelope(
                request=request,
                spec=spec,
                input_payload=normalized_input,
                pipeline_stages=pipeline_stages,
            )

        self._hooks.before_execution(request=request, spec=spec)
        try:
            raw_result = registered_tool.handler(request)
        except Exception as error:
            execution_error = ToolExecutionError(
                str(error),
                stage="execute",
                details={"tool_name": spec.name, "task_name": request.task.name},
            )
            self._hooks.on_execution_error(request=request, spec=spec, error=execution_error)
            raise execution_error from error

        legacy_result = self._coerce_legacy_result(
            request=request,
            spec=spec,
            raw_result=raw_result,
        )
        self._hooks.after_execution(request=request, result=legacy_result)
        try:
            runtime_result = self._standardize_runtime_result(
                request=request,
                spec=spec,
                input_payload=normalized_input,
                legacy_result=legacy_result,
                pipeline_stages=pipeline_stages,
                policy_decision=policy_decision,
            )
        except ToolValidationError as error:
            return self._build_validation_failure_envelope(
                request=request,
                spec=spec,
                input_payload=normalized_input,
                error=error,
                pipeline_stages=pipeline_stages,
            )
        runtime_protocol_raw = runtime_result.output_payload.get("runtime_protocol", {})
        runtime_protocol = (
            dict(cast(dict[str, object], runtime_protocol_raw))
            if isinstance(runtime_protocol_raw, dict)
            else {}
        )
        transcript_blocks = build_tool_transcript_blocks(
            request=request,
            spec=spec,
            input_payload=runtime_result.input_payload,
            output_payload=runtime_result.output_payload,
            status=runtime_result.status,
            runtime_protocol=runtime_protocol,
        )
        return ToolExecutionEnvelope(
            request=request,
            spec=spec,
            runtime_result=runtime_result,
            transcript_blocks=transcript_blocks,
            policy_decision=policy_decision,
            runtime_protocol=runtime_protocol,
        )

    def _build_validation_failure_envelope(
        self,
        *,
        request: ToolExecutionRequest,
        spec: ToolSpec,
        input_payload: dict[str, object],
        error: ToolValidationError,
        pipeline_stages: list[str],
    ) -> ToolExecutionEnvelope:
        output_payload = {
            "stdout": "",
            "stderr": str(error),
            "exit_code": 1,
            "validation_failed": True,
            "validation_stage": error.stage,
            "validation_details": dict(error.details),
        }
        runtime_protocol = self._build_runtime_protocol(
            spec=spec,
            pipeline_stages=pipeline_stages,
            policy_decision=None,
            input_payload=input_payload,
            output_payload=output_payload,
        )
        output_with_protocol = {**output_payload, "runtime_protocol": runtime_protocol}
        runtime_result = ToolRuntimeResult(
            spec=spec,
            source_type=self._default_source_type(spec),
            source_name=self._default_source_name(spec),
            command_or_action=self._default_command_or_action(request=request, spec=spec),
            input_payload=input_payload,
            output_payload=output_with_protocol,
            status=TaskNodeStatus.FAILED,
            started_at=request.started_at,
            ended_at=datetime.now(UTC),
        )
        transcript_blocks = build_tool_transcript_blocks(
            request=request,
            spec=spec,
            input_payload=input_payload,
            output_payload=output_with_protocol,
            status=TaskNodeStatus.FAILED,
            runtime_protocol=runtime_protocol,
        )
        return ToolExecutionEnvelope(
            request=request,
            spec=spec,
            runtime_result=runtime_result,
            transcript_blocks=transcript_blocks,
            runtime_protocol=runtime_protocol,
        )

    def _build_policy_denied_envelope(
        self,
        *,
        request: ToolExecutionRequest,
        spec: ToolSpec,
        input_payload: dict[str, object],
        decision: ToolPolicyDecision,
        pipeline_stages: list[str],
    ) -> ToolExecutionEnvelope:
        output_payload = {
            "stdout": "",
            "stderr": decision.reason or "tool execution denied by policy",
            "exit_code": 1,
            "policy_denied": True,
            "policy_allowed": False,
            "policy_reason": decision.reason,
            "policy_metadata": dict(decision.metadata),
        }
        runtime_protocol = self._build_runtime_protocol(
            spec=spec,
            pipeline_stages=pipeline_stages,
            policy_decision=decision,
            input_payload=input_payload,
            output_payload=output_payload,
        )
        output_with_protocol = {**output_payload, "runtime_protocol": runtime_protocol}
        runtime_result = ToolRuntimeResult(
            spec=spec,
            source_type=self._default_source_type(spec),
            source_name=self._default_source_name(spec),
            command_or_action=self._default_command_or_action(request=request, spec=spec),
            input_payload=input_payload,
            output_payload=output_with_protocol,
            status=TaskNodeStatus.FAILED,
            started_at=request.started_at,
            ended_at=datetime.now(UTC),
        )
        transcript_blocks = build_tool_transcript_blocks(
            request=request,
            spec=spec,
            input_payload=input_payload,
            output_payload=output_with_protocol,
            status=TaskNodeStatus.FAILED,
            runtime_protocol=runtime_protocol,
        )
        return ToolExecutionEnvelope(
            request=request,
            spec=spec,
            runtime_result=runtime_result,
            transcript_blocks=transcript_blocks,
            policy_decision=decision,
            runtime_protocol=runtime_protocol,
        )

    def _build_interaction_required_envelope(
        self,
        *,
        request: ToolExecutionRequest,
        spec: ToolSpec,
        input_payload: dict[str, object],
        pipeline_stages: list[str],
    ) -> ToolExecutionEnvelope:
        output_payload = {
            "stdout": "",
            "stderr": "tool execution blocked pending user interaction",
            "exit_code": 1,
            "execution_blocked": True,
            "interaction_required": True,
            "interrupt_behavior": spec.interrupt_behavior().value,
            "block_reason": "user_interaction_required",
        }
        runtime_protocol = self._build_runtime_protocol(
            spec=spec,
            pipeline_stages=pipeline_stages,
            policy_decision=None,
            input_payload=input_payload,
            output_payload=output_payload,
        )
        output_with_protocol = {**output_payload, "runtime_protocol": runtime_protocol}
        runtime_result = ToolRuntimeResult(
            spec=spec,
            source_type=self._default_source_type(spec),
            source_name=self._default_source_name(spec),
            command_or_action=self._default_command_or_action(request=request, spec=spec),
            input_payload=input_payload,
            output_payload=output_with_protocol,
            status=TaskNodeStatus.BLOCKED,
            started_at=request.started_at,
            ended_at=datetime.now(UTC),
        )
        transcript_blocks = build_tool_transcript_blocks(
            request=request,
            spec=spec,
            input_payload=input_payload,
            output_payload=output_with_protocol,
            status=TaskNodeStatus.BLOCKED,
            runtime_protocol=runtime_protocol,
        )
        return ToolExecutionEnvelope(
            request=request,
            spec=spec,
            runtime_result=runtime_result,
            transcript_blocks=transcript_blocks,
            runtime_protocol=runtime_protocol,
        )

    def _coerce_legacy_result(
        self,
        *,
        request: ToolExecutionRequest,
        spec: ToolSpec,
        raw_result: object,
    ) -> ToolExecutionResult:
        if isinstance(raw_result, ToolExecutionResult):
            return raw_result
        if isinstance(raw_result, ToolRuntimeResult):
            return ToolExecutionResult(
                spec=raw_result.spec,
                source_type=raw_result.source_type,
                source_name=raw_result.source_name,
                command_or_action=raw_result.command_or_action,
                input_payload=dict(raw_result.input_payload),
                output_payload=dict(raw_result.output_payload),
                status=raw_result.status,
                started_at=raw_result.started_at,
                ended_at=raw_result.ended_at,
            )
        raise ToolExecutionError(
            f"Unsupported tool result type: {type(raw_result).__name__}",
            stage="standardize_result",
            details={"tool_name": spec.name, "task_name": request.task.name},
        )

    def _standardize_runtime_result(
        self,
        *,
        request: ToolExecutionRequest,
        spec: ToolSpec,
        input_payload: dict[str, object],
        legacy_result: ToolExecutionResult,
        pipeline_stages: list[str],
        policy_decision: ToolPolicyDecision,
    ) -> ToolRuntimeResult:
        self._validate_schema(
            schema=spec.output_schema,
            payload=legacy_result.output_payload,
            stage="standardize_result",
            label="output",
        )
        runtime_protocol = self._build_runtime_protocol(
            spec=spec,
            pipeline_stages=pipeline_stages,
            policy_decision=policy_decision,
            input_payload=input_payload,
            output_payload=legacy_result.output_payload,
        )
        output_payload = dict(legacy_result.output_payload)
        output_payload["runtime_protocol"] = runtime_protocol
        return ToolRuntimeResult(
            spec=legacy_result.spec,
            source_type=legacy_result.source_type,
            source_name=legacy_result.source_name,
            command_or_action=legacy_result.command_or_action,
            input_payload=dict(legacy_result.input_payload),
            output_payload=output_payload,
            status=legacy_result.status,
            started_at=legacy_result.started_at,
            ended_at=legacy_result.ended_at,
        )

    def _build_runtime_protocol(
        self,
        *,
        spec: ToolSpec,
        pipeline_stages: list[str],
        policy_decision: ToolPolicyDecision | None,
        input_payload: dict[str, object],
        output_payload: dict[str, object],
    ) -> dict[str, object]:
        return {
            "version": "2.0",
            "tool_name": spec.name,
            "pipeline_stages": list(pipeline_stages),
            "interrupt_behavior": spec.interrupt_behavior().value,
            "requires_user_interaction": spec.requires_user_interaction(),
            "schema_validation": {
                "input_schema": dict(spec.input_schema),
                "output_schema": dict(spec.output_schema),
            },
            "policy": {
                "allowed": None if policy_decision is None else policy_decision.allowed,
                "reason": None if policy_decision is None else policy_decision.reason,
                "metadata": {} if policy_decision is None else dict(policy_decision.metadata),
            },
            "input_keys": sorted(input_payload.keys()),
            "output_keys": sorted(output_payload.keys()),
        }

    def _validate_schema(
        self,
        *,
        schema: dict[str, object],
        payload: object,
        stage: str,
        label: str,
    ) -> None:
        schema_type = schema.get("type")
        if isinstance(schema_type, str):
            self._validate_type(schema_type=schema_type, payload=payload, stage=stage, label=label)
        required = schema.get("required")
        if isinstance(required, list) and isinstance(payload, dict):
            for key in required:
                if isinstance(key, str) and key not in payload:
                    raise ToolValidationError(
                        f"{label} schema validation failed: missing required field '{key}'",
                        stage=stage,
                        details={"field": key, "label": label},
                    )
        properties = schema.get("properties")
        if isinstance(properties, dict) and isinstance(payload, dict):
            for key, child_schema in properties.items():
                if key in payload and isinstance(child_schema, dict):
                    self._validate_schema(
                        schema=child_schema,
                        payload=payload[key],
                        stage=stage,
                        label=f"{label}.{key}",
                    )
        items = schema.get("items")
        if isinstance(items, dict) and isinstance(payload, list):
            for index, item in enumerate(payload):
                self._validate_schema(
                    schema=items,
                    payload=item,
                    stage=stage,
                    label=f"{label}[{index}]",
                )

    def _validate_type(
        self,
        *,
        schema_type: str,
        payload: object,
        stage: str,
        label: str,
    ) -> None:
        validators: dict[str, type[object] | tuple[type[object], ...]] = {
            "object": dict,
            "array": list,
            "string": str,
            "integer": int,
            "number": (int, float),
            "boolean": bool,
        }
        expected = validators.get(schema_type)
        if expected is None:
            return
        if schema_type in {"integer", "number"} and isinstance(payload, bool):
            is_valid = False
        else:
            is_valid = isinstance(payload, expected)
        if not is_valid:
            raise ToolValidationError(
                f"{label} schema validation failed: expected {schema_type}",
                stage=stage,
                details={"expected_type": schema_type, "label": label},
            )

    @staticmethod
    def _default_source_type(spec: ToolSpec) -> str:
        return "coordinator" if spec.category.value == "orchestration" else "runtime"

    @staticmethod
    def _default_source_name(spec: ToolSpec) -> str:
        return (
            "workflow-engine" if spec.category.value == "orchestration" else "authorized-assessment"
        )

    @staticmethod
    def _default_command_or_action(*, request: ToolExecutionRequest, spec: ToolSpec) -> str:
        if spec.capability.value == "stage_transition":
            return f"transition:{request.task.name}"
        return f"execute:{request.task.name}"
