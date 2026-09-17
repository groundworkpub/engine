"""Groundwork Universal LLM Router & Multi-Provider Gateway (Version 2.1).

Synthesizes best practices from:
- langgenius/dify (Unified Model Gateway, parameter normalization, and provider abstraction)
- 0xzr/freellmpool (Free tier multi-provider rotation)
- GoSlowPoke168/hermes-openrouter-free-rotator (Dynamic round-robin rotation for :free models on 429)
- KashifKhn/gemini-proxy (Resilient fallbacks & circuit breaker)

Provides resilient functions:
- `call_llm(messages, response_format="text", max_tokens=4096)`
- `call_llm_json(messages, max_tokens=4096)`
with automatic circuit breakers, exponential backoff, Groq/Cloudflare/OpenRouter/Gemini failover,
and deterministic fallback parsing.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import threading
import time
import urllib.request
from collections.abc import Callable
from typing import Any, Literal

try:
    from dotenv import load_dotenv

    # Robust multi-path dotenv loading
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
    REPO_ROOT = os.path.dirname(SCRIPT_DIR)

    load_dotenv(os.path.join(REPO_ROOT, ".env.local"), override=True)
    load_dotenv(os.path.join(REPO_ROOT, ".env"), override=True)
    load_dotenv(".env.local", override=True)
    load_dotenv(".env", override=True)
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("llm_router")

OPENROUTER_FREE_MODELS = [
    "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free",
    "openrouter/nvidia/nemotron-3-super-120b-a12b:free",
    "openrouter/liquid/lfm-2.5-2.6b:free",
    "openrouter/cohere/north-mini-code:free",
]

OPENROUTER_PAID_MODELS = [
    "deepseek/deepseek-chat",
    "openai/gpt-4o-mini",
    "meta-llama/llama-3.3-70b-instruct",
    "deepseek/deepseek-r1",
]

CLOUDFLARE_MODELS = [
    "@cf/meta/llama-3.1-8b-instruct",
    "@cf/meta/llama-3.2-3b-instruct",
    "@cf/mistral/mistral-7b-instruct-v0.1",
]

GROQ_MODELS = [
    "qwen/qwen3.8-27b",
    "qwen/qwen3.6-27b",
    "openai/gpt-oss-120b",
]

HUGGINGFACE_MODELS = [
    "Qwen/Qwen2.5-72B-Instruct",
    "meta-llama/Llama-3.1-8B-Instruct",
]

GEMINI_MODELS = [
    "gemini-flash-latest",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-flash-lite-latest",
]


class LLMRouter:
    """Intelligent multi-provider LLM gateway with active failover and circuit breaker."""

    def __init__(self) -> None:
        self.cf_account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID")
        self.cf_email = os.getenv("CLOUDFLARE_EMAIL")
        self.cf_key = os.getenv("CLOUDFLARE_GLOBAL_API_KEY")
        self.gateway_id = os.getenv("CLOUDFLARE_GATEWAY_ID")  # AI Gateway E3
        self.openrouter_key = os.getenv("OPENROUTER_API_KEY")
        raw_groq = os.getenv("GROQ_API_KEYS") or os.getenv("GROQ_API_KEY") or ""
        self.groq_keys = [k.strip() for k in raw_groq.split(",") if k.strip()]
        self.groq_key = self.groq_keys[0] if self.groq_keys else None
        self._groq_key_idx = 0
        self.gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        self.hf_token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_API_KEY")
        self.failed_providers: dict[str, float] = {}  # provider_name -> cooloff_until_timestamp

    def _is_provider_healthy(self, provider_id: str) -> bool:
        """Check if provider is not currently tripped by circuit breaker."""
        cooloff = self.failed_providers.get(provider_id, 0)
        if time.time() < cooloff:
            return False
        return True

    def _trip_circuit_breaker(self, provider_id: str, cooloff_seconds: int = 180) -> None:
        """Temporarily isolate a failing provider."""
        logger.warning(f"Tripping circuit breaker for provider [{provider_id}] for {cooloff_seconds}s.")
        self.failed_providers[provider_id] = time.time() + cooloff_seconds

    # ─── Provider 0: Cloudflare AI Gateway (E3 observability + universal route) ─

    def _call_ai_gateway(
        self,
        messages: list[dict[str, str]],
        model: str = "@cf/meta/llama-3.1-8b-instruct",
        max_tokens: int = 4096,
    ) -> str | None:
        """Route via Cloudflare AI Gateway (OpenAI-compatible) for logging, caching, fallback.

        Wrap any configured upstream (Workers AI, OpenRouter, Groq) into a single
        gateway endpoint so failed/cached calls show in AI Gateway analytics — free tier.
        """
        if not self.cf_account_id or not self.cf_key or not self.gateway_id:
            return None
        url = (
            f"https://gateway.ai.cloudflare.com/v1/{self.cf_account_id}/"
            f"{self.gateway_id}/openai/chat/completions"
        )
        payload = json.dumps(
            {"model": model, "messages": messages, "max_tokens": max_tokens}
        ).encode()
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "X-Auth-Email": self.cf_email or "",
                "X-Auth-Key": self.cf_key,
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=35) as resp:
                data = json.loads(resp.read().decode())
                choices = data.get("choices")
                if choices and isinstance(choices, list):
                    msg = choices[0].get("message", {})
                    content = msg.get("content")
                    if content:
                        return str(content)
        except Exception as e:
            logger.debug(f"AI Gateway call failed for {model}: {e}")
        return None

    # ─── Provider 1: Cloudflare Workers AI (Direct Native) ──────────────────

    def _call_cloudflare_ai(
        self,
        messages: list[dict[str, str]],
        model: str = "@cf/meta/llama-3.1-8b-instruct",
        max_tokens: int = 4096,
    ) -> str | None:
        if not self.cf_account_id or not self.cf_key:
            return None

        url = f"https://api.cloudflare.com/client/v4/accounts/{self.cf_account_id}/ai/run/{model}"
        payload = json.dumps({"messages": messages, "max_tokens": max_tokens}).encode()
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "X-Auth-Email": self.cf_email or "",
                "X-Auth-Key": self.cf_key,
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=35) as resp:
                data = json.loads(resp.read().decode())
                result = data.get("result", {})
                if isinstance(result, dict):
                    # Current OpenAI-compatible shape: result.choices[0].message.content
                    choices = result.get("choices")
                    if choices and isinstance(choices, list):
                        msg = choices[0].get("message", {})
                        content = msg.get("content")
                        if content:
                            return str(content)
                    # Legacy shape fallback: result.response
                    legacy = result.get("response", "")
                    if isinstance(legacy, dict):
                        return json.dumps(legacy)
                    if legacy:
                        return str(legacy)
                return ""
        except Exception as e:
            logger.warning(f"Cloudflare AI ({model}) error: {e}")
            return None

    # ─── Provider 2: Groq Direct API (Sub-Second Latency) ────────────────────

    def _call_groq(
        self,
        messages: list[dict[str, str]],
        model: str = "qwen/qwen3.8-27b",
        max_tokens: int = 4096,
    ) -> str | None:
        keys_to_try = self.groq_keys if self.groq_keys else ([self.groq_key] if self.groq_key else [])
        if not keys_to_try:
            return None

        url = "https://api.groq.com/openai/v1/chat/completions"
        payload = json.dumps({
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.7,
        }).encode()

        num_keys = len(keys_to_try)
        start_idx = self._groq_key_idx
        self._groq_key_idx = (self._groq_key_idx + 1) % num_keys

        for i in range(num_keys):
            gkey = keys_to_try[(start_idx + i) % num_keys]
            req = urllib.request.Request(
                url,
                data=payload,
                headers={
                    "Authorization": f"Bearer {gkey}",
                    "Content-Type": "application/json",
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=25) as resp:
                    data = json.loads(resp.read().decode())
                    choices = data.get("choices", [])
                    if choices:
                        return choices[0].get("message", {}).get("content", "")
            except Exception as e:
                logger.warning(f"Groq API ({model}) with key {gkey[:10]}... failed: {e}. Trying next key...")
                continue
        return None

    # ─── Provider 3: OpenRouter Dynamic Rotator ──────────────────────────────

    def _call_openrouter(
        self,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int = 4096,
    ) -> str | None:
        if not self.openrouter_key:
            return None

        clean_model = model.replace("openrouter/", "")
        url = "https://openrouter.ai/api/v1/chat/completions"
        payload = json.dumps({
            "model": clean_model,
            "messages": messages,
            "max_tokens": max_tokens,
        }).encode()

        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.openrouter_key}",
                "HTTP-Referer": "https://gworky.com",
                "X-Title": "Groundwork Media",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
                choices = data.get("choices", [])
                if choices:
                    return choices[0].get("message", {}).get("content", "")
                return None
        except Exception as e:
            logger.warning(f"OpenRouter ({model}) failed: {e}")
            return None

    # ─── Provider 4: Google Gemini API (Optional with Circuit Breaker) ───────

    def _call_gemini(
        self,
        messages: list[dict[str, str]] | str,
        model: str = "gemini-3.5-flash",
        max_tokens: int = 4096,
    ) -> str | None:
        if not self.gemini_key:
            return None

        if isinstance(messages, str):
            messages = [{"role": "user", "content": messages}]

        # Build combined prompt from messages
        combined_prompt = "\n\n".join(f"{m.get('role', 'user').upper()}: {m.get('content', '')}" for m in messages)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        payload = json.dumps({
            "contents": [{"parts": [{"text": combined_prompt}]}],
            "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.7},
        }).encode()

        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": self.gemini_key,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                data = json.loads(resp.read().decode())
                candidates = data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts:
                        return parts[0].get("text", "")
                return None
        except Exception as e:
            logger.warning(f"Gemini API ({model}) notice: {e}")
            return None

    # ─── Provider 6: Hugging Face Serverless Inference Client ($0 USD) ─────────

    def _call_huggingface(
        self,
        messages: list[dict[str, str]],
        model: str = "Qwen/Qwen2.5-72B-Instruct",
        max_tokens: int = 4096,
    ) -> str | None:
        """Execute serverless LLM inference via Hugging Face Hub Client ($0 USD)."""
        if not self.hf_token:
            return None
        try:
            from huggingface_hub import InferenceClient

            client = InferenceClient(token=self.hf_token)
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
            )
            if resp.choices:
                return str(resp.choices[0].message.content)
        except Exception as e:
            logger.debug(f"Hugging Face inference call failed for {model}: {e}")
        return None

    # ─── Public Gateway Method ───────────────────────────────────────────────

    def _call_with_deadline(self, fn: Callable[[], str | None], deadline_s: float) -> str | None:
        """Execute a provider call with a hard wall-clock deadline.

        urllib's socket timeout does not cap slow-trickling streams (a reasoning
        model can run 9+ minutes while emitting tokens). This wrapper abandons
        any attempt that exceeds its budget so one slow model cannot starve the
        whole pipeline run.
        """
        outcome: dict[str, str | None] = {}

        def runner() -> None:
            try:
                outcome["value"] = fn()
            except Exception as e:
                logger.warning(f"Provider call raised: {e}")
                outcome["value"] = None

        worker = threading.Thread(target=runner, daemon=True)
        worker.start()
        worker.join(deadline_s)
        if worker.is_alive():
            logger.warning(f"Provider call abandoned after exceeding {deadline_s:.0f}s wall-clock deadline.")
            return None
        return outcome.get("value")

    def generate(
        self,
        messages: list[dict[str, str]] | str,
        response_format: Literal["text", "json"] = "text",
        max_tokens: int = 4096,
        temperature: float = 0.7,
        time_budget_s: float = 240.0,
        prefer_provider: str | None = None,
    ) -> str | None:
        """Execute resilient inference query through multi-tier pool with automatic failover."""
        if isinstance(messages, str):
            messages = [{"role": "user", "content": messages}]

        if prefer_provider is None:
            prefer_provider = os.getenv("PRIMARY_LLM_PROVIDER", "groq")

        start_time = time.time()
        deadline = start_time + time_budget_s

        def remaining() -> float:
            return deadline - time.time()

        def exhausted() -> bool:
            if remaining() <= 0:
                logger.warning(f"LLM router wall-clock budget ({time_budget_s:.0f}s) exhausted; aborting failover chain.")
                return True
            return False

        def _try_groq() -> str | None:
            if self._is_provider_healthy("groq_api") and (self.groq_keys or self.groq_key):
                for g_model in GROQ_MODELS:
                    if exhausted():
                        return None
                    logger.info(f"Attempting inference with Groq: {g_model}")
                    raw = self._call_with_deadline(
                        lambda m=g_model: self._call_groq(messages, model=m, max_tokens=max_tokens),
                        deadline_s=remaining(),
                    )
                    if raw and len(raw.strip()) > 10:
                        latency = round(time.time() - start_time, 2)
                        logger.info(f"Inference succeeded via Groq ({g_model}) in {latency}s.")
                        return raw
                self._trip_circuit_breaker("groq_api", 120)
            return None

        def _try_gemini() -> str | None:
            if self._is_provider_healthy("gemini_api") and self.gemini_key:
                for g_model in GEMINI_MODELS:
                    if exhausted():
                        return None
                    logger.info(f"Attempting inference with Google Gemini: {g_model}")
                    raw = self._call_with_deadline(
                        lambda m=g_model: self._call_gemini(messages, model=m, max_tokens=max_tokens),
                        deadline_s=remaining(),
                    )
                    if raw and len(raw.strip()) > 10:
                        latency = round(time.time() - start_time, 2)
                        logger.info(f"Inference succeeded via Gemini API ({g_model}) in {latency}s.")
                        return raw
                self._trip_circuit_breaker("gemini_api", 180)
            return None

        def _try_cloudflare() -> str | None:
            if self._is_provider_healthy("ai_gateway") and self.gateway_id:
                for gw_model in CLOUDFLARE_MODELS:
                    if exhausted():
                        return None
                    raw = self._call_with_deadline(
                        lambda m=gw_model: self._call_ai_gateway(messages, model=m, max_tokens=max_tokens),
                        deadline_s=remaining(),
                    )
                    if raw and len(raw.strip()) > 10:
                        latency = round(time.time() - start_time, 2)
                        logger.info(f"Inference succeeded via AI Gateway ({gw_model}) in {latency}s.")
                        return raw
                self._trip_circuit_breaker("ai_gateway", 120)

            if self._is_provider_healthy("cloudflare_ai"):
                for cf_model in CLOUDFLARE_MODELS:
                    if exhausted():
                        return None
                    logger.info(f"Attempting inference with Cloudflare AI: {cf_model}")
                    raw = self._call_with_deadline(
                        lambda m=cf_model: self._call_cloudflare_ai(messages, model=m, max_tokens=max_tokens),
                        deadline_s=remaining(),
                    )
                    if raw and len(raw.strip()) > 10:
                        latency = round(time.time() - start_time, 2)
                        logger.info(f"Inference succeeded via Cloudflare AI ({cf_model}) in {latency}s.")
                        return raw
                self._trip_circuit_breaker("cloudflare_ai", 120)
            return None

        def _try_openrouter() -> str | None:
            if self._is_provider_healthy("openrouter_free") and self.openrouter_key:
                shuffled_models = list(OPENROUTER_FREE_MODELS)
                random.shuffle(shuffled_models)
                for or_model in shuffled_models:
                    if exhausted():
                        return None
                    logger.info(f"Attempting failover inference with OpenRouter: {or_model}")
                    raw = self._call_with_deadline(
                        lambda m=or_model: self._call_openrouter(messages, model=m, max_tokens=max_tokens),
                        deadline_s=remaining(),
                    )
                    if raw and len(raw.strip()) > 10:
                        latency = round(time.time() - start_time, 2)
                        logger.info(f"Inference succeeded via OpenRouter ({or_model}) in {latency}s.")
                        return raw
                self._trip_circuit_breaker("openrouter_free", 180)
            return None

        def _try_huggingface() -> str | None:
            if self._is_provider_healthy("huggingface_api") and self.hf_token:
                for hf_model in HUGGINGFACE_MODELS:
                    if exhausted():
                        return None
                    logger.info(f"Attempting inference with Hugging Face: {hf_model}")
                    raw = self._call_with_deadline(
                        lambda m=hf_model: self._call_huggingface(messages, model=m, max_tokens=max_tokens),
                        deadline_s=remaining(),
                    )
                    if raw and len(raw.strip()) > 10:
                        latency = round(time.time() - start_time, 2)
                        logger.info(f"Inference succeeded via Hugging Face ({hf_model}) in {latency}s.")
                        return raw
                self._trip_circuit_breaker("huggingface_api", 180)
            return None

        # Build order based on preference (Groq primary by default as agreed in architecture)
        if prefer_provider == "groq":
            tiers = [_try_groq, _try_gemini, _try_cloudflare, _try_openrouter, _try_huggingface]
        else:
            tiers = [_try_cloudflare, _try_groq, _try_openrouter, _try_huggingface, _try_gemini]

        for tier_fn in tiers:
            if exhausted():
                break
            res = tier_fn()
            if res:
                return res

        logger.error("All LLM providers in the multi-tier pool failed or were rate-limited.")
        return None

    def generate_json(
        self,
        messages: list[dict[str, str]] | str,
        max_tokens: int = 4096,
        time_budget_s: float = 240.0,
    ) -> dict[str, Any] | None:
        """Generate and parse structured JSON reliably using brace-depth tracking and json_repair."""
        if isinstance(messages, str):
            messages = [{"role": "user", "content": messages}]

        # Ensure system prompt instructs raw JSON output
        system_appended = False
        for msg in messages:
            if msg.get("role") == "system":
                msg["content"] += "\nReturn strictly raw JSON format without preamble or backticks."
                system_appended = True
                break
        if not system_appended:
            messages.insert(0, {"role": "system", "content": "You are a JSON generator. Return strictly raw JSON."})

        raw_output = self.generate(messages, response_format="json", max_tokens=max_tokens, time_budget_s=time_budget_s)
        if not raw_output:
            return None

        # Strip reasoning-model think tokens (e.g. <think>...</think>) which break JSON parsing
        raw_output = re.sub(r"<think>.*?</think>", "", raw_output, flags=re.DOTALL | re.IGNORECASE)
        raw_output = re.sub(r"<\|?thinking\|?>.*?<\|?/?thinking\|?>", "", raw_output, flags=re.DOTALL)

        # Clean markdown codeblocks
        clean_text = re.sub(r"^```(?:json)?\s*", "", raw_output.strip(), flags=re.IGNORECASE)
        clean_text = re.sub(r"\s*```$", "", clean_text)

        # 1. cJSON-inspired outermost balanced JSON bracket extractor
        def extract_balanced_json(text: str) -> str | None:
            start_idx = -1
            brace_type = None
            depth = 0
            in_string = False
            escape = False

            for i, ch in enumerate(text):
                if escape:
                    escape = False
                    continue
                if ch == "\\":
                    escape = True
                    continue
                if ch == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue

                if ch in ("{", "["):
                    if depth == 0:
                        start_idx = i
                        brace_type = ch
                    depth += 1
                elif ch in ("}", "]"):
                    if (brace_type == "{" and ch == "}") or (brace_type == "[" and ch == "]"):
                        depth -= 1
                        if depth == 0 and start_idx != -1:
                            return text[start_idx : i + 1]
            return None

        extracted = extract_balanced_json(clean_text) or clean_text

        # 2. Strict standard JSON parse
        try:
            parsed = json.loads(extracted, strict=False)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

        # 3. Robust json_repair fallback
        try:
            import json_repair
            parsed = json_repair.loads(extracted)
            if isinstance(parsed, dict):
                return parsed
            parsed_raw = json_repair.loads(raw_output)
            if isinstance(parsed_raw, dict):
                return parsed_raw
        except Exception as err:
            logger.debug(f"json_repair failed: {err}")

        logger.error(f"Failed to decode JSON from LLM output. Preview: {raw_output[:200]}")
        return None


# Global singleton instance
router = LLMRouter()


def call_llm(
    messages: list[dict[str, str]] | str,
    response_format: Literal["text", "json"] = "text",
    max_tokens: int = 4096,
    prefer_provider: str | None = None,
) -> str | None:
    """Convenience helper function for agent pipeline."""
    return router.generate(messages, response_format=response_format, max_tokens=max_tokens, prefer_provider=prefer_provider)


def call_llm_json(
    messages: list[dict[str, str]] | str,
    max_tokens: int = 4096,
) -> dict[str, Any] | None:
    """Convenience helper for generating JSON data structures."""
    return router.generate_json(messages, max_tokens=max_tokens)
