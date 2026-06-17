"""Linkit analyser — OpenRouter LLM calls with model routing (Hawk-style)."""

import json
import os
import time
from pathlib import Path
from threading import Lock

import httpx

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
ROUTING_PATH = Path(__file__).resolve().parent / "model_routing.json"

DEFAULT_ROUTING = {
    "analyser": "openai/gpt-oss-120b:free",
}

FALLBACK_MODELS = ["openrouter/free"]


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


class OpenRouterLimiter:
    """Throttle OpenRouter calls to avoid 429s on the free tier."""

    def __init__(self):
        self._lock = Lock()
        self._timestamps: list[float] = []
        self._run_count = 0

    def acquire(self) -> str | None:
        max_per_run = _env_int("OR_MAX_REQUESTS_PER_RUN", 20)
        max_per_min = _env_int("OR_MAX_REQUESTS_PER_MINUTE", 8)
        min_interval = _env_int("OR_MIN_REQUEST_INTERVAL_SECONDS", 2)

        with self._lock:
            if self._run_count >= max_per_run:
                return "run_limit_reached"
            now = time.time()
            self._timestamps = [t for t in self._timestamps if now - t < 60]
            if len(self._timestamps) >= max_per_min:
                wait = 60 - (now - self._timestamps[0]) + 0.5
                if wait > 0:
                    time.sleep(wait)
                now = time.time()
                self._timestamps = [t for t in self._timestamps if now - t < 60]
            if self._timestamps:
                gap = min_interval - (now - self._timestamps[-1])
                if gap > 0:
                    time.sleep(gap)
            self._timestamps.append(time.time())
            self._run_count += 1
        return None


_limiter = OpenRouterLimiter()


def _openrouter_keys() -> list[str]:
    return [
        k for i in range(1, 6)
        if (k := os.getenv(f"OR_KEY_{i}", "").strip())
    ]


def load_model_routing() -> dict:
    if ROUTING_PATH.exists():
        return {**DEFAULT_ROUTING, **json.loads(ROUTING_PATH.read_text())}
    return dict(DEFAULT_ROUTING)


def _post_openrouter(
    key: str,
    model: str,
    system: str,
    user_content: str,
    max_tokens: int,
) -> httpx.Response:
    return httpx.post(
        OPENROUTER_URL,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/linkit-analyser",
            "X-Title": "Linkit Fundability Analyser",
        },
        json={
            "model": model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
        },
        timeout=45,
    )


def call_openrouter(
    system: str,
    user_content: str,
    role: str = "analyser",
    max_tokens: int = 600,
) -> dict:
    """Call OpenRouter with role-based model routing and free-tier fallbacks."""
    keys = _openrouter_keys()
    if not keys:
        return {"error": "no_openrouter_keys", "text": "", "model_used": "rule_fallback"}

    limit_err = _limiter.acquire()
    if limit_err:
        return {"error": limit_err, "text": "", "model_used": "rule_fallback"}

    routing = load_model_routing()
    model = routing.get(role, DEFAULT_ROUTING["analyser"])
    models_to_try = []
    for m in [model, *FALLBACK_MODELS]:
        if m not in models_to_try:
            models_to_try.append(m)

    max_retries = _env_int("OR_MAX_RETRIES_PER_KEY", 3)
    retry_delay = _env_int("OR_RETRY_DELAY_SECONDS", 10)
    last_err = "request_failed"

    for try_model in models_to_try:
        for key in keys:
            for attempt in range(max_retries):
                try:
                    r = _post_openrouter(key, try_model, system, user_content, max_tokens)
                    if r.status_code == 429:
                        last_err = "rate_limited_429"
                        time.sleep(retry_delay)
                        continue
                    if r.status_code in (404, 502, 503):
                        last_err = r.text[:200]
                        break
                    if r.status_code != 200:
                        return {"error": r.text[:200], "text": "", "model_used": try_model}
                    choices = r.json().get("choices") or []
                    if not choices:
                        last_err = "empty_response"
                        break
                    text = choices[0]["message"]["content"]
                    return {"text": text, "model_used": try_model}
                except Exception as e:
                    last_err = str(e)
                    if attempt + 1 < max_retries:
                        time.sleep(retry_delay)
                    continue
    return {"error": last_err, "text": "", "model_used": model}
