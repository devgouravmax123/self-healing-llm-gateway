"""LiteLLM provider service module for executing LLM completions."""

import logging
from typing import Any, Literal, cast

import litellm

from app.core.config import Settings, settings
from app.core.exceptions import ProviderError
from app.models.provider import ProviderTarget
from app.models.requests import ChatCompletionRequest
from app.models.responses import (
    ChatCompletionChoice,
    ChatCompletionMessageResponse,
    ChatCompletionResponse,
    CompletionUsage,
)
from app.reliability.error_classifier import error_classifier

logger = logging.getLogger(__name__)

# Suppress verbose default logging from LiteLLM
litellm.suppress_debug_info = True


class LiteLLMService:
    """Service wrapper for LiteLLM completion calls."""

    def __init__(self, config: Settings | None = None) -> None:
        self.config = config or settings

    def _resolve_model_and_params(
        self,
        request: ChatCompletionRequest,
        target: ProviderTarget | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Resolve the model name and extra parameters for LiteLLM based on the target provider."""
        provider = (target.provider if target else self.config.llm_provider).lower()
        extra_kwargs: dict[str, Any] = {}

        if provider == "ollama":
            # Determine target model from target or request
            if target is not None:
                model_name = target.model
            else:
                raw_model = request.model
                fallback_models = ("default", "local-model-a")
                model_name = self.config.ollama_model if raw_model in fallback_models else raw_model

            if not model_name.startswith("ollama/"):
                formatted_model = f"ollama/{model_name}"
            else:
                formatted_model = model_name

            api_base = (
                target.api_base if target and target.api_base else self.config.ollama_base_url
            )
            extra_kwargs["api_base"] = api_base
            return formatted_model, extra_kwargs

        # Default fallback for other providers
        model_str = target.model if target else request.model
        return model_str, extra_kwargs

    async def execute_chat_completion(
        self,
        request: ChatCompletionRequest,
        request_id: str,
        target: ProviderTarget | None = None,
    ) -> ChatCompletionResponse:
        """Execute chat completion through LiteLLM and convert to ChatCompletionResponse."""
        model_name, extra_kwargs = self._resolve_model_and_params(request, target=target)

        # Convert Pydantic ChatMessage list to dict format required by litellm
        messages = [{"role": msg.role, "content": msg.content} for msg in request.messages]

        kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            **extra_kwargs,
        }

        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.max_tokens is not None:
            kwargs["max_tokens"] = request.max_tokens

        # Pass timeout to LiteLLM call
        kwargs["timeout"] = self.config.provider_timeout

        provider_name = target.provider if target else self.config.llm_provider
        try:
            raw_response = await litellm.acompletion(**kwargs)
            return self._convert_response(raw_response, request.model, request_id)
        except Exception as exc:
            classified = error_classifier.classify(exc, provider=provider_name)
            logger.error(
                "Upstream provider failure: category=%s, type=%s, provider=%s, retryable=%s",
                classified.category.value,
                classified.original_type,
                classified.provider,
                classified.retryable,
            )
            raise ProviderError(
                message=classified.message,
                status_code=classified.http_status,
                provider=classified.provider,
                category=classified.category.value,
                retryable=classified.retryable,
                details={
                    "error_category": classified.category.value,
                    "original_type": classified.original_type,
                    "retryable": classified.retryable,
                    **classified.details,
                },
            ) from exc

    def _convert_response(
        self,
        raw: Any,
        requested_model: str,
        request_id: str,
    ) -> ChatCompletionResponse:
        """Convert LiteLLM ModelResponse into ChatCompletionResponse."""
        # Response ID: preserve request_id format or litellm id
        response_id = getattr(raw, "id", None) or f"chatcmpl-{request_id}"

        choices: list[ChatCompletionChoice] = []
        raw_choices = getattr(raw, "choices", []) or []

        for idx, choice in enumerate(raw_choices):
            message_obj = getattr(choice, "message", None)
            if message_obj is not None:
                role = getattr(message_obj, "role", "assistant") or "assistant"
                content = getattr(message_obj, "content", "") or ""
            elif isinstance(choice, dict):
                msg_dict = choice.get("message", {})
                role = msg_dict.get("role", "assistant")
                content = msg_dict.get("content", "")
            else:
                role = "assistant"
                content = ""

            raw_finish = str(getattr(choice, "finish_reason", "stop") or "stop")
            finish_reason: Literal["stop", "length", "tool_calls", "content_filter"]
            if raw_finish in ("stop", "length", "tool_calls", "content_filter"):
                finish_reason = cast(
                    Literal["stop", "length", "tool_calls", "content_filter"], raw_finish
                )
            else:
                finish_reason = "stop"

            choices.append(
                ChatCompletionChoice(
                    index=getattr(choice, "index", idx) or idx,
                    message=ChatCompletionMessageResponse(
                        role=role,
                        content=content,
                    ),
                    finish_reason=finish_reason,
                )
            )

        if not choices:
            choices.append(
                ChatCompletionChoice(
                    index=0,
                    message=ChatCompletionMessageResponse(
                        role="assistant",
                        content="",
                    ),
                    finish_reason="stop",
                )
            )

        raw_usage = getattr(raw, "usage", None)
        usage: CompletionUsage | None = None
        if raw_usage is not None:
            prompt_tokens = getattr(raw_usage, "prompt_tokens", None)
            if prompt_tokens is not None:
                prompt_tokens = int(prompt_tokens)

            completion_tokens = getattr(raw_usage, "completion_tokens", None)
            if completion_tokens is not None:
                completion_tokens = int(completion_tokens)

            total_tokens = getattr(raw_usage, "total_tokens", None)
            if total_tokens is not None:
                total_tokens = int(total_tokens)

            usage = CompletionUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
            )
        else:
            usage = None

        created = getattr(raw, "created", None)
        created_int = int(created) if created is not None else None

        resp_kwargs: dict[str, Any] = {
            "id": response_id,
            "model": requested_model,
            "choices": choices,
            "usage": usage,
        }
        if created_int is not None:
            resp_kwargs["created"] = created_int

        return ChatCompletionResponse(**resp_kwargs)


# Global service instance
litellm_service = LiteLLMService()
