"""OpenAI-compatible Token Factory client, with a recorded client when no key is set."""

from __future__ import annotations

import hashlib
import json
import os
import random
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

import requests
from pydantic import BaseModel, ValidationError

from codesentinel.nemotron.catalog import DEFAULT_BASE_URL, LIGHTNING, NANO
from codesentinel.nemotron.schemas import (
    ADJUDICATION_SCHEMA,
    PHYSICIAN_QUERY_SCHEMA,
    TRIAGE_SCHEMA,
    AdjudicationResult,
    PhysicianQueryResult,
    TriageResult,
)

RETRY_ATTEMPTS = 3
_RETRYABLE_NETWORK = (requests.Timeout, requests.ConnectionError, TimeoutError)


def failure_reason(*, stage: str, model: str, exc: BaseException) -> str:
    """Status, exception class, model, and stage. No prompt, key, or response body."""
    status = ""
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None) if response is not None else None
    if isinstance(code, int):
        status = str(code)
    return f"stage={stage} model={model} status={status} error={type(exc).__name__}"


SCHEMA_BY_NAME = {
    "triage": (TRIAGE_SCHEMA, TriageResult),
    "adjudication": (ADJUDICATION_SCHEMA, AdjudicationResult),
    "physician_query": (PHYSICIAN_QUERY_SCHEMA, PhysicianQueryResult),
}


@dataclass
class Completion:
    data: Optional[BaseModel]
    raw: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    mock: bool
    usage_source: str
    model: str
    error: Optional[str] = None
    repaired: bool = False
    fallback: bool = False


def extract_json(text: str) -> Any:
    raw = (text or "").strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            return json.loads(raw[start : end + 1])
        raise


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


# models.list is checked once per process and base URL, for MODELS_CACHE_TTL_S. A slow
# catalog call is retried once before the triage model falls back to the catalog id.
MODELS_LIST_TIMEOUT_S = 45
MODELS_LIST_ATTEMPTS = 2
MODELS_CACHE_TTL_S = 600
_MODELS_CACHE: dict[str, tuple[float, list[str]]] = {}


def reset_models_cache() -> None:
    _MODELS_CACHE.clear()


class NemotronClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        transport: Optional[Callable] = None,
        get_transport: Optional[Callable] = None,
        sleep: Optional[Callable[[float], None]] = None,
        record_dir: Optional[str] = None,
        replay: bool = False,
        rng: Optional[Callable[[], float]] = None,
    ):
        if api_key is None:
            api_key = os.environ.get("NEBIUS_API_KEY", "")
        self.api_key = (api_key or "").strip()
        configured = base_url or os.environ.get("NEBIUS_BASE_URL") or DEFAULT_BASE_URL
        self.base_url = configured.rstrip("/") + "/"
        self.mock = not self.api_key
        self._transport = transport or requests.post
        self._get = get_transport or requests.get
        self._sleep = sleep or time.sleep
        self._rng = rng or random.random
        self._lock = threading.Lock()
        self.degraded_reasons: list[str] = []
        self.used_fallback = False
        self.available_models: Optional[list[str]] = None
        self._models_checked = False
        self.triage_model = LIGHTNING
        self.last_ratelimit_remaining_requests: Optional[str] = None
        self.last_ratelimit_remaining_tokens: Optional[str] = None
        self.record_dir = Path(record_dir) if record_dir else None
        self.replay = replay or os.environ.get("CODESENTINEL_REPLAY") == "1"

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _note_headers(self, headers) -> None:
        if not headers:
            return
        remaining_requests = headers.get("x-ratelimit-remaining-requests")
        remaining_tokens = headers.get("x-ratelimit-remaining-tokens")
        with self._lock:
            if remaining_requests is not None:
                self.last_ratelimit_remaining_requests = remaining_requests
            if remaining_tokens is not None:
                self.last_ratelimit_remaining_tokens = remaining_tokens

    def _record_degraded(self, message: str) -> None:
        with self._lock:
            self.degraded_reasons.append(message)
            self.used_fallback = True

    def models_list(self) -> list[str]:
        if self.mock:
            from codesentinel.nemotron.catalog import SUPER, ULTRA

            return [LIGHTNING, NANO, SUPER, ULTRA]
        response = self._get(
            self.base_url + "models",
            headers=self._headers(),
            timeout=MODELS_LIST_TIMEOUT_S,
        )
        self._note_headers(getattr(response, "headers", {}))
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            return []
        return [str(row.get("id")) for row in rows if isinstance(row, dict) and row.get("id")]

    def ensure_models(self) -> None:
        if self.mock or self._models_checked:
            return
        self._models_checked = True
        key = self.base_url
        cached = _MODELS_CACHE.get(key) if self._get is requests.get else None
        if cached and time.monotonic() - cached[0] < MODELS_CACHE_TTL_S:
            found = list(cached[1])
        else:
            found = None
            last_exc: Optional[Exception] = None
            for attempt in range(MODELS_LIST_ATTEMPTS):
                try:
                    found = self.models_list()
                    break
                except Exception as exc:  # network, auth, parse
                    last_exc = exc
                    if attempt + 1 < MODELS_LIST_ATTEMPTS:
                        self._sleep(1.0)
            if found is None:
                self.available_models = None
                self.triage_model = LIGHTNING
                with self._lock:
                    self.degraded_reasons.append(f"models.list failed: {last_exc}")
                return
            if self._get is requests.get:
                _MODELS_CACHE[key] = (time.monotonic(), list(found))
        self.available_models = found
        if LIGHTNING in found:
            self.triage_model = LIGHTNING
        elif NANO in found:
            self.triage_model = NANO
        else:
            self.triage_model = LIGHTNING
            with self._lock:
                self.degraded_reasons.append(
                    "models.list did not include Lightning or Nano; calling the catalog Lightning id"
                )

    def select_triage_model(self) -> str:
        self.ensure_models()
        return self.triage_model

    def complete_json(
        self,
        *,
        model: str,
        messages: list[dict],
        schema_name: str,
        schema: dict,
        pydantic_model: type[BaseModel],
        temperature: float,
        reasoning: bool,
        mock_context: Optional[dict] = None,
        stage: Optional[str] = None,
    ) -> Completion:
        stage_name = stage or schema_name
        if self.mock:
            return self._mock_complete(model, messages, schema_name, mock_context or {})
        try:
            return self._live_complete(
                model=model,
                messages=messages,
                schema_name=schema_name,
                schema=schema,
                pydantic_model=pydantic_model,
                temperature=temperature,
                reasoning=reasoning,
                stage=stage_name,
            )
        except Exception as exc:
            reason = failure_reason(stage=stage_name, model=model, exc=exc)
            self._record_degraded(reason)
            fallback = self._mock_complete(model, messages, schema_name, mock_context or {})
            fallback.fallback = True
            fallback.error = reason
            return fallback

    def _mock_complete(self, model: str, messages: list[dict], schema_name: str, mock_context: dict) -> Completion:
        from codesentinel.nemotron.mock_reasoner import mock_payload

        started = time.perf_counter()
        data = mock_payload(schema_name, mock_context)
        raw = data.model_dump_json()
        prompt = json.dumps(messages, default=str)
        return Completion(
            data=data,
            raw=raw,
            input_tokens=estimate_tokens(prompt),
            output_tokens=estimate_tokens(raw),
            latency_ms=int((time.perf_counter() - started) * 1000),
            mock=True,
            usage_source="estimated",
            model=model,
        )

    def _live_complete(
        self,
        *,
        model: str,
        messages: list[dict],
        schema_name: str,
        schema: dict,
        pydantic_model: type[BaseModel],
        temperature: float,
        reasoning: bool,
        stage: str,
    ) -> Completion:
        first = self._post_completion(model, messages, schema_name, schema, temperature, reasoning, stage)
        parsed = _validate(pydantic_model, first.raw)
        if parsed is not None:
            first.data = parsed
            return first
        repair_messages = list(messages) + [
            {"role": "assistant", "content": first.raw or ""},
            {
                "role": "user",
                "content": (
                    "That output failed schema validation. Return only one JSON object matching the schema. "
                    "Do not add commentary."
                ),
            },
        ]
        second = self._post_completion(model, repair_messages, schema_name, schema, temperature, reasoning, stage)
        second.input_tokens += first.input_tokens
        second.output_tokens += first.output_tokens
        second.latency_ms += first.latency_ms
        second.repaired = True
        parsed = _validate(pydantic_model, second.raw)
        second.data = parsed
        if parsed is None:
            second.error = "schema validation failed after one repair retry"
        return second

    def _post_completion(
        self,
        model: str,
        messages: list[dict],
        schema_name: str,
        schema: dict,
        temperature: float,
        reasoning: bool,
        stage: str,
    ) -> Completion:
        response_format = {
            "type": "json_schema",
            "json_schema": {"name": schema_name, "strict": True, "schema": schema},
        }
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "response_format": response_format,
        }
        if reasoning:
            body["chat_template_kwargs"] = {"enable_thinking": True, "low_effort": True}
            body["reasoning_effort"] = "low"
        else:
            body["chat_template_kwargs"] = {"enable_thinking": False}

        digest = _request_digest(model, messages, temperature, response_format, reasoning)
        if self.replay and self.record_dir is not None:
            recorded = self._read_recording(digest, messages)
            if recorded is not None:
                return recorded

        last_error: Optional[Exception] = None
        for attempt in range(RETRY_ATTEMPTS):
            started = time.perf_counter()
            try:
                response = self._transport(
                    self.base_url + "chat/completions",
                    headers=self._headers(),
                    json=body,
                    timeout=90,
                )
            except _RETRYABLE_NETWORK as exc:
                last_error = exc
                if attempt < RETRY_ATTEMPTS - 1:
                    self._sleep(self._retry_delay(attempt, None))
                    continue
                raise
            latency_ms = int((time.perf_counter() - started) * 1000)
            self._note_headers(getattr(response, "headers", {}))
            status = getattr(response, "status_code", 200)
            retryable = status == 429 or (isinstance(status, int) and 500 <= status <= 599)
            if retryable and attempt < RETRY_ATTEMPTS - 1:
                self._sleep(self._retry_delay(attempt, response))
                continue
            try:
                response.raise_for_status()
            except Exception as exc:
                if getattr(exc, "response", None) is None:
                    try:
                        exc.response = response  # type: ignore[attr-defined]
                    except Exception:
                        pass
                last_error = exc
                raise
            payload = response.json()
            content = ""
            choices = payload.get("choices") or []
            if choices:
                content = ((choices[0] or {}).get("message") or {}).get("content") or ""
            usage = payload.get("usage") or {}
            input_tokens = int(usage.get("prompt_tokens") or estimate_tokens(json.dumps(messages)))
            output_tokens = int(usage.get("completion_tokens") or estimate_tokens(content))
            usage_source = "provider" if "prompt_tokens" in usage else "estimated"
            completion = Completion(
                data=None,
                raw=content,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
                mock=False,
                usage_source=usage_source,
                model=model,
            )
            self._write_recording(digest, content, usage if isinstance(usage, dict) else {}, model)
            return completion
        raise last_error or RuntimeError(f"Token Factory request failed at stage {stage}")

    def _retry_delay(self, attempt: int, response) -> float:
        """Retry-After when the server sets one, otherwise exponential backoff plus jitter."""
        headers = getattr(response, "headers", {}) or {}
        raw_after = headers.get("Retry-After") if response is not None else None
        try:
            retry_after = float(raw_after) if raw_after is not None else 0.0
        except (TypeError, ValueError):
            retry_after = 0.0
        if retry_after > 0:
            return retry_after
        return min(2**attempt, 8) + (float(self._rng()) * 0.25)

    def _read_recording(self, digest: str, messages: list) -> Optional[Completion]:
        path = self.record_dir / f"{digest}.json"
        if not path.exists():
            return None
        stored = json.loads(path.read_text(encoding="utf-8"))
        content = stored.get("content") or ""
        usage = stored.get("usage") or {}
        if not isinstance(usage, dict):
            usage = {}
        input_tokens = int(usage.get("prompt_tokens") or estimate_tokens(json.dumps(messages, default=str)))
        output_tokens = int(usage.get("completion_tokens") or estimate_tokens(content))
        return Completion(
            data=None,
            raw=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=0,
            mock=False,
            usage_source="provider" if "prompt_tokens" in usage else "estimated",
            model=str(stored.get("model") or ""),
        )

    def _write_recording(self, digest: str, content: str, usage: dict, model: str) -> None:
        if self.record_dir is None:
            return
        self.record_dir.mkdir(parents=True, exist_ok=True)
        payload = {"content": content, "usage": usage, "model": model}
        (self.record_dir / f"{digest}.json").write_text(
            json.dumps(payload),
            encoding="utf-8",
        )


def _request_digest(model: str, messages: list, temperature: float, response_format: dict, reasoning: bool) -> str:
    blob = json.dumps(
        {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "response_format": response_format,
            "reasoning": reasoning,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _validate(model: type[BaseModel], raw: str) -> Optional[BaseModel]:
    try:
        payload = extract_json(raw)
    except (json.JSONDecodeError, ValueError, TypeError):
        return None
    try:
        return model.model_validate(payload)
    except ValidationError:
        return None
