"""
Unified LLM provider layer for AnchorClaim.

One place to configure the model that powers the whole product:
  * contract parsing (step1_parse_contract),
  * the independent evidence-reasoning agents and their vote (step2b_reason_evidence),
  * the Q&A assistant (qna_agent).

Three provider presets, selectable from the Settings page in the UI:
  * openai  -> OpenAI (or any OpenAI-compatible /chat/completions endpoint),
  * claude  -> Anthropic's Claude (/messages),
  * custom  -> any self-hosted / third-party OpenAI-compatible endpoint.

Configuration precedence (highest first):
  1. ``.anchorclaim_settings.json`` written by the Settings page,
  2. environment variables loaded from ``.env`` (OPENAI_API_KEY, ANTHROPIC_API_KEY,
     GEMINI_API_KEY, ...).

There are no SDK requirements — every call is a plain HTTPS POST, so this works
on serverless (Vercel) exactly like locally. When no provider is configured, or a
call fails, each consumer falls back to its deterministic offline path.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import urllib.error
import urllib.request

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo root

# "kind" decides the wire format the endpoint speaks. "custom" reuses the OpenAI
# chat-completions format against whatever base URL the user supplies.
PROVIDERS = {
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "env_key": "OPENAI_API_KEY",
        "kind": "openai",
    },
    "claude": {
        "label": "Claude (Anthropic)",
        "base_url": "https://api.anthropic.com/v1",
        "model": "claude-haiku-4-5-20251001",
        "env_key": "ANTHROPIC_API_KEY",
        "kind": "anthropic",
    },
    "custom": {
        "label": "Custom API",
        "base_url": "",
        "model": "",
        "env_key": "ANCHORCLAIM_API_KEY",
        "kind": "openai",
    },
}

DEFAULT_PROVIDER = "openai"
REQUEST_TIMEOUT = 30

_SETTINGS_CANDIDATES = [
    os.path.join(_ROOT, ".anchorclaim_settings.json"),
    os.path.join(tempfile.gettempdir(), "anchorclaim_settings.json"),
]


def _extract_json(text: str) -> dict:
    """Pull a JSON object out of an LLM response, tolerating ```json fences and prose."""
    cleaned = re.sub(r"```(?:json)?", "", text or "").strip()
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON found in output:\n{cleaned[:400]}")
    return json.loads(match.group(0))


# ---------------------------------------------------------------------------
# Settings persistence (the Settings page in the UI writes this file).
# ---------------------------------------------------------------------------
def load_settings() -> dict:
    """Read the saved settings dict; empty strings where nothing is configured."""
    settings = {"provider": "", "base_url": "", "api_key": "", "model": ""}
    for path in _SETTINGS_CANDIDATES:
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for key in settings:
                    settings[key] = str(data.get(key) or "").strip()
            break
        except (OSError, ValueError):
            continue
    return settings


def save_settings(data: dict) -> dict:
    """Persist settings and return the saved (normalised) dict.

    Leaving ``api_key`` blank keeps the previously saved key for the same provider
    (the UI never echoes a key back, so a blank field means "unchanged"). Pass
    ``clear_key: true`` to explicitly remove the stored key.
    """
    settings = {
        "provider": str(data.get("provider") or "openai").strip(),
        "base_url": str(data.get("base_url") or "").strip(),
        "api_key": str(data.get("api_key") or "").strip(),
        "model": str(data.get("model") or "").strip(),
    }
    if settings["provider"] not in PROVIDERS:
        settings["provider"] = DEFAULT_PROVIDER

    if bool(data.get("clear_key")):
        settings["api_key"] = ""
    elif not settings["api_key"]:
        previous = load_settings()
        if previous.get("provider") == settings["provider"] and previous.get("api_key"):
            settings["api_key"] = previous["api_key"]

    target = _SETTINGS_CANDIDATES[0]
    try:
        with open(target, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
    except OSError:
        target = _SETTINGS_CANDIDATES[1]
        try:
            with open(target, "w", encoding="utf-8") as f:
                json.dump(settings, f, indent=2)
        except OSError:
            pass
    return settings


def resolve(settings: dict | None = None) -> dict:
    """Return a fully-resolved config: preset defaults + saved key + env fallback."""
    s = dict(settings if settings is not None else load_settings())
    provider = (s.get("provider") or DEFAULT_PROVIDER).strip()
    if provider not in PROVIDERS:
        provider = DEFAULT_PROVIDER
    preset = PROVIDERS[provider]

    resolved = {
        "provider": provider,
        "kind": preset["kind"],
        "base_url": (s.get("base_url") or preset["base_url"]).strip(),
        "model": (s.get("model") or preset["model"]).strip(),
        "api_key": (s.get("api_key") or "").strip(),
    }
    if not resolved["api_key"]:
        resolved["api_key"] = os.getenv(preset["env_key"], "").strip()
    return resolved


def is_configured(settings: dict | None = None) -> bool:
    return bool(resolve(settings)["api_key"])


def public_status() -> dict:
    """Masked view of the current config, safe to return to the browser."""
    r = resolve()
    key = r["api_key"]
    return {
        "provider": r["provider"],
        "kind": r["kind"],
        "base_url": r["base_url"],
        "model": r["model"],
        "configured": bool(key),
        "api_key_set": bool(key),
        "api_key_hint": (key[-4:] if len(key) >= 4 else "") if key else "",
    }


# ---------------------------------------------------------------------------
# Raw completions (plain HTTPS — no SDK).
# ---------------------------------------------------------------------------
def _chat_url(base_url: str) -> str:
    b = (base_url or "").strip().rstrip("/")
    if b.endswith("/chat/completions"):
        return b
    if b.endswith("/v1"):
        return b + "/chat/completions"
    return b + "/v1/chat/completions"


def _post(url: str, payload: dict, headers: dict) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def openai_chat(prompt: str, base_url: str, api_key: str, model: str, system: str | None = None) -> str:
    messages = ([{"role": "system", "content": system}] if system else []) + \
               [{"role": "user", "content": prompt}]
    body = _post(
        _chat_url(base_url),
        {"model": model, "messages": messages, "temperature": 0.2, "stream": False},
        {"Authorization": f"Bearer {api_key}"},
    )
    return body["choices"][0]["message"]["content"]


def anthropic_chat(prompt: str, base_url: str, api_key: str, model: str, system: str | None = None) -> str:
    b = (base_url or "").strip().rstrip("/")
    payload = {
        "model": model,
        "max_tokens": 1024,
        "temperature": 0.2,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        payload["system"] = system
    body = _post(
        b + "/messages", payload,
        {"x-api-key": api_key, "anthropic-version": "2023-06-01"},
    )
    return body["content"][0]["text"]


def gemini_chat(prompt: str, api_key: str, model: str) -> str:
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
           f"?key={api_key}")
    body = _post(
        url,
        {"contents": [{"parts": [{"text": prompt}]}],
         "generationConfig": {"response_mime_type": "application/json"}},
        {},
    )
    return body["candidates"][0]["content"]["parts"][0]["text"]


def complete(agent: dict, prompt: str, system: str | None = None) -> str:
    """Route one agent config to the right wire format and return raw text."""
    if agent["kind"] == "anthropic":
        return anthropic_chat(prompt, agent["base_url"], agent["api_key"], agent["model"], system)
    if agent["kind"] == "gemini":
        return gemini_chat(prompt, agent["api_key"], agent["model"])
    return openai_chat(prompt, agent["base_url"], agent["api_key"], agent["model"], system)


def chat_json(prompt: str, settings: dict | None = None, system: str | None = None) -> dict:
    """One-shot JSON completion through the configured provider (used by step 1)."""
    r = resolve(settings)
    text = complete(r, prompt, system)
    return _extract_json(text)


# ---------------------------------------------------------------------------
# Agent discovery for the multi-agent vote (step 2b).
# ---------------------------------------------------------------------------
def configured_agents() -> list[dict]:
    """Return one entry per distinct provider that has a key.

    The Settings page configures a single primary provider; the other vendors can
    still participate in the vote when their keys are present in the environment.
    """
    primary = resolve()
    agents: list[dict] = []
    if primary["api_key"]:
        agents.append({
            "name": primary["provider"],
            "kind": primary["kind"],
            "base_url": primary["base_url"],
            "api_key": primary["api_key"],
            "model": primary["model"],
        })

    seen = {a["name"] for a in agents}
    seen_kinds = {a["kind"] for a in agents}
    env_vendors = (
        ("openai", "openai", "OPENAI_API_KEY", "https://api.openai.com/v1", "OPENAI_MODEL", "gpt-4o-mini"),
        ("anthropic", "anthropic", "ANTHROPIC_API_KEY", "https://api.anthropic.com/v1", "ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"),
        ("gemini", "gemini", "GEMINI_API_KEY", "https://generativelanguage.googleapis.com/v1beta", "GEMINI_MODEL", "gemini-2.0-flash"),
    )
    for name, kind, key_env, base, model_env, default_model in env_vendors:
        key = os.getenv(key_env, "").strip()
        if key and name not in seen and kind not in seen_kinds:
            agents.append({
                "name": name,
                "kind": kind,
                "base_url": base,
                "api_key": key,
                "model": os.getenv(model_env, default_model).strip(),
            })
            seen.add(name)
            seen_kinds.add(kind)
    return agents