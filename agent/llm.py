"""
Unified LLM provider layer for AnchorClaim.

One place to configure the models that power the whole product:
  * contract parsing (step1_parse_contract) — uses the first configured provider,
  * the independent evidence-reasoning agents and their vote (step2b_reason_evidence)
    — every configured provider votes, and a claim needs a strict majority,
  * the Q&A assistant (qna_agent) — uses the first OpenAI-compatible provider.

The Settings page in the UI manages a *list* of providers, so several can coexist
(e.g. OpenAI + one or more custom / self-hosted OpenAI-compatible endpoints, plus
Claude). Each entry stores a wire format:

  * "openai"  -> OpenAI-compatible /chat/completions (OpenAI, DeepSeek, OpenRouter,
                 Groq, Ollama /v1, any self-hosted vLLM/TGI server, ...),
  * "claude"  -> Anthropic's /messages.

Configuration precedence (highest first):
  1. ``.anchorclaim_settings.json`` written by the Settings page,
  2. environment variables loaded from ``.env`` (OPENAI_API_KEY, ANTHROPIC_API_KEY,
     GEMINI_API_KEY — the latter only participates via env, it has no UI preset).

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
import uuid

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo root

# Wire-format defaults per stored kind (used when a field is left blank).
KIND_DEFAULTS = {
    "openai": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
    "claude": {"base_url": "https://api.anthropic.com/v1", "model": "claude-haiku-4-5-20251001"},
}

REQUEST_TIMEOUT = 30
USER_AGENT = "AnchorClaim/1.0 (+python-urllib)"


class ProviderError(Exception):
    """An LLM provider call failed (HTTP status or network), with server detail attached."""


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


def _wire_kind(kind: str) -> str:
    """Map a stored kind ("openai"/"claude"/"custom") to a wire format."""
    k = (kind or "openai").strip().lower()
    if k in ("claude", "anthropic"):
        return "anthropic"
    return "openai"


def _new_id() -> str:
    return "p" + uuid.uuid4().hex[:10]


def _default_label(kind: str) -> str:
    return "Claude" if _wire_kind(kind) == "anthropic" else "OpenAI-compatible"


# ---------------------------------------------------------------------------
# Settings persistence — a list of providers, one entry per configured vendor.
# ---------------------------------------------------------------------------
def _read_file() -> dict | None:
    for path in _SETTINGS_CANDIDATES:
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            continue
    return None


def _migrate_old(data: dict) -> list[dict]:
    """Convert the pre-multi-provider single-provider shape into a one-entry list."""
    if "provider" in data:
        provider = str(data.get("provider") or "openai").strip()
        kind = "claude" if provider == "claude" else "openai"
        return [{
            "id": _new_id(),
            "label": provider if provider in ("openai", "claude") else "Custom API",
            "kind": kind,
            "base_url": str(data.get("base_url") or "").strip(),
            "model": str(data.get("model") or "").strip(),
            "api_key": str(data.get("api_key") or "").strip(),
        }]
    return []


def load_settings() -> dict:
    """Return ``{"providers": [entry, ...]}`` — the full list including any saved keys."""
    data = _read_file()
    if data is None:
        return {"providers": []}
    if "providers" in data:
        raw = data["providers"]
        if isinstance(raw, list):
            return {"providers": [_normalize_entry(e) for e in raw]}
    migrated = _migrate_old(data)
    return {"providers": migrated}


def _normalize_entry(entry: dict) -> dict:
    kind = str(entry.get("kind") or "openai").strip().lower()
    if kind not in ("openai", "claude"):
        kind = "openai"
    defaults = KIND_DEFAULTS[kind]
    eid = str(entry.get("id") or _new_id()).strip() or _new_id()
    label = str(entry.get("label") or "").strip() or _default_label(kind)
    base_url = str(entry.get("base_url") or "").strip() or defaults["base_url"]
    model = str(entry.get("model") or "").strip() or defaults["model"]
    return {
        "id": eid,
        "label": label,
        "kind": kind,
        "base_url": base_url,
        "model": model,
        "api_key": str(entry.get("api_key") or "").strip(),
    }


def save_settings(data: dict) -> None:
    """Persist the provider list.

    For each entry, a blank ``api_key`` keeps the previously saved key for the same
    ``id`` (the UI never echoes a key back, so blank means "unchanged"). Set
    ``clear_key: true`` on an entry to explicitly remove its stored key.
    """
    if not isinstance(data, dict):
        return
    previous = {p["id"]: p for p in load_settings()["providers"]}
    raw = data.get("providers")
    if not isinstance(raw, list):
        return

    providers: list[dict] = []
    for entry in raw:
        e = _normalize_entry(entry)
        if bool(entry.get("clear_key")):
            e["api_key"] = ""
        elif not e["api_key"]:
            old = previous.get(e["id"])
            if old and old.get("api_key"):
                e["api_key"] = old["api_key"]
        providers.append(e)

    payload = json.dumps({"providers": providers}, indent=2)
    for target in _SETTINGS_CANDIDATES:
        try:
            with open(target, "w", encoding="utf-8") as f:
                f.write(payload)
            return
        except OSError:
            continue


# ---------------------------------------------------------------------------
# Agent discovery.
# ---------------------------------------------------------------------------
def entry_to_agent(entry: dict) -> dict:
    """Turn one provider entry into the {name, kind, base_url, api_key, model}
    shape the completion functions consume. ``kind`` is already a wire format."""
    e = _normalize_entry(entry)
    return {
        "name": e["label"],
        "kind": _wire_kind(e["kind"]),
        "base_url": e["base_url"],
        "api_key": e["api_key"],
        "model": e["model"],
    }


def configured_agents() -> list[dict]:
    """Every provider that actually has a key, plus env-only vendors.

    Settings-page entries are used as-is (all of them — several custom endpoints
    coexist and vote independently). The env vendors only join if their key is set
    and no settings entry already speaks the same wire format (so a config on the
    page doesn't get double-weighted by an env key of the same kind).
    """
    agents: list[dict] = []
    covered: set[str] = set()
    for p in load_settings()["providers"]:
        if p.get("api_key"):
            agents.append(entry_to_agent(p))
            covered.add(agents[-1]["kind"])

    env_vendors = (
        ("openai", "openai", "OPENAI_API_KEY", "https://api.openai.com/v1", "OPENAI_MODEL", "gpt-4o-mini"),
        ("anthropic", "anthropic", "ANTHROPIC_API_KEY", "https://api.anthropic.com/v1", "ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"),
        ("gemini", "gemini", "GEMINI_API_KEY", "https://generativelanguage.googleapis.com/v1beta", "GEMINI_MODEL", "gemini-2.0-flash"),
    )
    for name, kind, key_env, base, model_env, default_model in env_vendors:
        key = os.getenv(key_env, "").strip()
        if key and (kind == "gemini" or kind not in covered):
            agents.append({
                "name": name,
                "kind": kind,
                "base_url": base,
                "api_key": key,
                "model": os.getenv(model_env, default_model).strip(),
            })
            covered.add(kind)
    return agents


def primary_agent() -> dict | None:
    agents = configured_agents()
    return agents[0] if agents else None


def is_configured() -> bool:
    return bool(configured_agents())


def public_status() -> dict:
    """Masked view of the full provider list, safe to return to the browser."""
    entries = []
    for p in load_settings()["providers"]:
        key = p.get("api_key") or ""
        entries.append({
            "id": p["id"],
            "label": p["label"],
            "kind": p["kind"],
            "base_url": p["base_url"],
            "model": p["model"],
            "api_key_set": bool(key),
            "api_key_hint": (key[-4:] if len(key) >= 4 else "") if key else "",
        })
    agents = configured_agents()
    return {
        "providers": entries,
        "configured": bool(agents),
        "agents": [a["name"] for a in agents],
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
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            **headers,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace").strip()[:400]
        except Exception:  # noqa: BLE001
            detail = ""
        msg = f"HTTP {e.code} {e.reason}"
        if detail:
            msg += f" — {detail}"
        raise ProviderError(msg) from e
    except urllib.error.URLError as e:
        raise ProviderError(f"unreachable — {e.reason}") from e


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


def chat_json(prompt: str, agent: dict | None = None, system: str | None = None) -> dict:
    """One-shot JSON completion (used by step 1). Uses the first configured provider
    when no specific agent is passed."""
    if agent is None:
        agent = primary_agent()
        if agent is None:
            raise RuntimeError("no configured AI provider")
    text = complete(agent, prompt, system)
    return _extract_json(text)