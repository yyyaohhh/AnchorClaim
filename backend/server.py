"""
AnchorClaim backend (Python / Flask).
Serves the UI and exposes the audit engine over HTTP.

Run:
  pip install flask
  python backend/server.py
  open http://localhost:8788
"""

import os
import sys
import json
import hashlib
import tempfile
from urllib.parse import parse_qs, urlencode
from dotenv import load_dotenv
from flask import Flask, jsonify, send_from_directory, request

# make agent modules importable
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "agent"))

load_dotenv(os.path.join(ROOT, ".env"))  # picks up API keys / RPC config before the agent modules read them

from audit_pipeline import audit_voyage       # noqa: E402
from samples import SAMPLE_VOYAGES            # noqa: E402
from fleet import fleet_overview, build_fleet, PORTS as PORT_COORDS  # noqa: E402
from qna_agent import ask_question            # noqa: E402
from step1_parse_contract import parse_contract  # noqa: E402
from llm import chat_json, entry_to_agent, load_settings, public_status, save_settings  # noqa: E402
import lifecycle                              # noqa: E402
from lifecycle import (                       # noqa: E402
    contract_template, funding_status, sign, reset_funding, monitoring_log,
)

FLEET_VOYAGES = {v["id"]: v for v in build_fleet()}
ALL_VOYAGES = {**SAMPLE_VOYAGES, **FLEET_VOYAGES}

FRONTEND = os.path.join(ROOT, "frontend")
app = Flask(__name__, static_folder=FRONTEND, static_url_path="")


class _VercelPathMiddleware:
    """Transparently restore the /api/<sub> path on Vercel.

    vercel.json rewrites ``/api/<sub>`` to ``/api/index.py?__path__=<sub>`` so
    the single serverless function is reached. That rewritten destination is what
    the WSGI layer sees as PATH_INFO (``/api/index.py``), so this middleware reads
    ``__path__`` back out of the query string and rebuilds the original path
    before Flask routing runs. Locally (no ``__path__``) it is a no-op.
    """

    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "")
        if path.rstrip("/") in ("/api/index.py", "/api/index"):
            params = parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=True)
            sub = (params.pop("__path__", None) or [""])[0]
            environ["QUERY_STRING"] = urlencode(params, doseq=True)
            environ["PATH_INFO"] = "/api/" + sub.lstrip("/") if sub else "/api"
        return self.wsgi_app(environ, start_response)


app.wsgi_app = _VercelPathMiddleware(app.wsgi_app)

# ---------------------------------------------------------------------------
# Persistent audit cache.
# Analyzing a voyage runs the LLM parse + settlement pipeline, which is slow.
# Results are cached to disk keyed by a hash of the voyage inputs, so an already
# analyzed vessel is served instantly and survives server restarts. The key
# includes the input hash, so editing a voyage's contract invalidates its entry.
#
# Serverless runtimes (Vercel, AWS Lambda, ...) expose a read-only filesystem
# with only the system temp dir writable. Writing under the project root would
# raise OSError at import time and 500 every /api request, so the cache dir is
# chosen defensively: .cache locally, transparently falling back to /tmp when
# the project directory cannot be written.
# ---------------------------------------------------------------------------
def _resolve_cache_dir():
    override = os.environ.get("ANCHORCLAIM_CACHE_DIR")
    if override:
        return override
    local = os.path.join(ROOT, ".cache")
    try:
        os.makedirs(local, exist_ok=True)
        # makedirs(exist_ok=True) does not fail on an existing read-only dir,
        # so probe writability explicitly before committing to it.
        probe = os.path.join(local, ".probe")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("ok")
        os.remove(probe)
        return local
    except OSError:
        fallback = os.path.join(tempfile.gettempdir(), "anchorclaim_cache")
        os.makedirs(fallback, exist_ok=True)
        return fallback


CACHE_DIR = _resolve_cache_dir()
lifecycle.configure(CACHE_DIR)


def _input_hash(voyage_id):
    payload = json.dumps(ALL_VOYAGES[voyage_id], sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def _cache_path(voyage_id):
    return os.path.join(CACHE_DIR, f"{voyage_id}_{_input_hash(voyage_id)}.json")


def _cache_get(voyage_id):
    path = _cache_path(voyage_id)
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None
    return None


def _cache_put(voyage_id, result):
    try:
        with open(_cache_path(voyage_id), "w", encoding="utf-8") as f:
            json.dump(result, f)
    except OSError:
        pass


@app.route("/")
def index():
    return send_from_directory(FRONTEND, "index.html")


def _voyage_card(voyage_id, v):
    lat, lng = PORT_COORDS.get(v["port"], [10.0, 115.0])
    return {
        "id": voyage_id,
        "vessel": v["vessel"],
        "imo": v["imo"],
        "port": v["port"],
        "lat": lat,
        "lng": lng,
        "deposit": v["escrow"]["deposit"],
        "freight": v["escrow"]["freight"],
        "contract_text": v["contract_text"],
        "cached": _cache_get(voyage_id) is not None,
    }


@app.route("/api/voyages")
@app.route("/voyages")
def voyages():
    out = [_voyage_card(vid, v) for vid, v in SAMPLE_VOYAGES.items()]
    return jsonify({"voyages": out})


@app.route("/api/voyage/<voyage_id>")
@app.route("/voyage/<voyage_id>")
def voyage(voyage_id):
    v = ALL_VOYAGES.get(voyage_id)
    if v is None:
        return jsonify({"error": "voyage not found"}), 404
    return jsonify(_voyage_card(voyage_id, v))


@app.route("/api/audit/<voyage_id>")
@app.route("/audit/<voyage_id>")
def audit(voyage_id):
    if voyage_id not in ALL_VOYAGES:
        return jsonify({"error": "voyage not found"}), 404

    # ?refresh=1 forces a re-run even if a cached result exists
    force = request.args.get("refresh") == "1"
    if not force:
        cached = _cache_get(voyage_id)
        if cached is not None:
            cached["_cached"] = True
            return jsonify(cached)

    try:
        result = audit_voyage(voyage_id, ALL_VOYAGES[voyage_id])
        _cache_put(voyage_id, result)
        result["_cached"] = False
        return jsonify(result)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 500


@app.route("/api/audit/<voyage_id>", methods=["POST"])
@app.route("/audit/<voyage_id>", methods=["POST"])
def audit_edited(voyage_id):
    """Re-audit a voyage with a contract edited live in the UI.

    The edited text is parsed fresh (watch the numbers change), and the result is
    never cached, so each edit is a real run against the engine.
    """
    if voyage_id not in ALL_VOYAGES:
        return jsonify({"error": "voyage not found"}), 404
    body = request.get_json(silent=True) or {}
    contract_text = body.get("contract_text")
    if not contract_text:
        return jsonify({"error": "contract_text required"}), 400
    try:
        result = audit_voyage(voyage_id, ALL_VOYAGES[voyage_id], contract_override=contract_text)
        result["_cached"] = False
        result["_edited"] = True
        return jsonify(result)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 500


@app.route("/api/fund/<voyage_id>")
@app.route("/fund/<voyage_id>")
def fund_state(voyage_id):
    """Everything the 'Verify & Fund' panel needs: the smart-contract template
    to confirm, the current signature status, and the daily monitoring log
    once both parties have signed."""
    if voyage_id not in ALL_VOYAGES:
        return jsonify({"error": "voyage not found"}), 404
    v = ALL_VOYAGES[voyage_id]
    contract = parse_contract(v["contract_text"])
    status = funding_status(voyage_id)
    return jsonify({
        "template": contract_template(voyage_id, v, contract),
        "status": status,
        "monitoring": monitoring_log(v) if status["funded"] else [],
    })


@app.route("/api/fund/<voyage_id>/sign", methods=["POST"])
@app.route("/fund/<voyage_id>/sign", methods=["POST"])
def fund_sign(voyage_id):
    """A wallet signature confirming the escrow fund() call (mock — no real chain call)."""
    if voyage_id not in ALL_VOYAGES:
        return jsonify({"error": "voyage not found"}), 404
    body = request.get_json(silent=True) or {}
    party = body.get("party")
    try:
        status = sign(voyage_id, party)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    v = ALL_VOYAGES[voyage_id]
    return jsonify({"status": status, "monitoring": monitoring_log(v) if status["funded"] else []})


@app.route("/api/fund/<voyage_id>", methods=["DELETE"])
@app.route("/fund/<voyage_id>", methods=["DELETE"])
def fund_reset(voyage_id):
    """Clear both signatures so the funding flow can be replayed."""
    reset_funding(voyage_id)
    return jsonify({"reset": True})


@app.route("/api/fleet")
@app.route("/fleet")
def fleet():
    """Fleet-scale roll-up: dozens of voyages summarised into status + totals."""
    return jsonify(fleet_overview())


@app.route("/api/qna", methods=["POST"])
def qna():
    """Q&A agent: answer questions about the project and blockchain.

    Body: {messages: [{role, content}], base_url?, api_key?, model?}
    The last message is the question; earlier messages form the conversation history.
    """
    body = request.get_json(silent=True) or {}
    messages = body.get("messages") or []
    if not isinstance(messages, list) or not messages:
        return jsonify({"error": "messages required"}), 400
    question = (messages[-1].get("content") or "").strip()
    if not question:
        return jsonify({"error": "question required"}), 400
    try:
        reply = ask_question(
            question=question,
            history=messages[:-1],
            base_url=body.get("base_url"),
            api_key=body.get("api_key"),
            model=body.get("model"),
        )
        return jsonify({"reply": reply})
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 500


@app.route("/api/cache", methods=["DELETE"])
@app.route("/cache", methods=["DELETE"])
def clear_cache():
    """Clear all cached audit results."""
    removed = 0
    try:
        entries = os.listdir(CACHE_DIR)
    except OSError:
        entries = []
    for fn in entries:
        if fn.endswith(".json"):
            try:
                os.remove(os.path.join(CACHE_DIR, fn))
                removed += 1
            except OSError:
                pass
    return jsonify({"cleared": removed})


@app.route("/api/settings", methods=["GET"])
@app.route("/settings", methods=["GET"])
def get_settings():
    """Current LLM provider config for the Settings page (API key never echoed back)."""
    return jsonify(public_status())


@app.route("/api/settings", methods=["POST"])
@app.route("/settings", methods=["POST"])
def set_settings():
    """Persist the LLM provider list chosen in the Settings page.

    Body: {providers: [{id?, label?, kind: "openai"|"claude", base_url?, model?,
    api_key?, clear_key?}]}. Several providers can coexist and all vote.
    """
    body = request.get_json(silent=True) or {}
    save_settings(body)
    return jsonify({"saved": public_status()})


@app.route("/api/settings/test", methods=["POST"])
@app.route("/settings/test", methods=["POST"])
def test_settings():
    """Ping every provider entry the Settings page is currently editing.

    Body: {providers: [{id?, label?, kind, base_url?, model?, api_key?, clear_key?}]}.
    Returns one {name, ok, error?} per entry. A blank key falls back to the saved
    key for the same id so a user can re-test a provider without retyping it.
    """
    body = request.get_json(silent=True) or {}
    raw = body.get("providers")
    if not isinstance(raw, list):
        raw = [body] if body else []
    if not raw:
        return jsonify({"ok": False, "error": "No providers provided"}), 400

    saved = {p["id"]: p for p in load_settings()["providers"]}
    results = []
    for e in raw:
        agent = entry_to_agent(e)
        if not agent["api_key"] and e.get("id") and e["id"] in saved:
            agent["api_key"] = saved[e["id"]].get("api_key") or ""
        if not agent["api_key"]:
            results.append({"name": agent["name"], "ok": False, "error": "no API key"})
            continue
        try:
            chat_json('Reply with the JSON object {"ok": true}. Return raw JSON only.', agent=agent)
            results.append({"name": agent["name"], "ok": True})
        except Exception as ex:  # noqa: BLE001
            results.append({"name": agent["name"], "ok": False, "error": str(ex)})
    return jsonify({"results": results})


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8788"))
    print(f"AnchorClaim API + UI on http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
