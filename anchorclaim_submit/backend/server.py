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
from flask import Flask, jsonify, send_from_directory, request

# make agent modules importable
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "agent"))

from audit_pipeline import audit_voyage       # noqa: E402
from samples import SAMPLE_VOYAGES            # noqa: E402

FRONTEND = os.path.join(ROOT, "frontend")
app = Flask(__name__, static_folder=FRONTEND, static_url_path="")

# ---------------------------------------------------------------------------
# Persistent audit cache.
# Analyzing a voyage runs the LLM parse + settlement pipeline, which is slow.
# Results are cached to disk keyed by a hash of the voyage inputs, so an already
# analyzed vessel is served instantly and survives server restarts. The key
# includes the input hash, so editing a voyage's contract invalidates its entry.
# ---------------------------------------------------------------------------
CACHE_DIR = os.path.join(ROOT, ".cache")
os.makedirs(CACHE_DIR, exist_ok=True)


def _input_hash(voyage_id):
    payload = json.dumps(SAMPLE_VOYAGES[voyage_id], sort_keys=True).encode("utf-8")
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


@app.route("/api/voyages")
def voyages():
    out = []
    for vid, v in SAMPLE_VOYAGES.items():
        out.append({
            "id": vid,
            "vessel": v["vessel"],
            "imo": v["imo"],
            "port": v["port"],
            "deposit": v["escrow"]["deposit"],
            "freight": v["escrow"]["freight"],
            "contract_text": v["contract_text"],
            "cached": _cache_get(vid) is not None,
        })
    return jsonify({"voyages": out})


@app.route("/api/audit/<voyage_id>")
def audit(voyage_id):
    if voyage_id not in SAMPLE_VOYAGES:
        return jsonify({"error": "voyage not found"}), 404

    # ?refresh=1 forces a re-run even if a cached result exists
    force = request.args.get("refresh") == "1"
    if not force:
        cached = _cache_get(voyage_id)
        if cached is not None:
            cached["_cached"] = True
            return jsonify(cached)

    try:
        result = audit_voyage(voyage_id, SAMPLE_VOYAGES[voyage_id])
        _cache_put(voyage_id, result)
        result["_cached"] = False
        return jsonify(result)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 500


@app.route("/api/audit/<voyage_id>", methods=["POST"])
def audit_edited(voyage_id):
    """Re-audit a voyage with a contract edited live in the UI.

    The edited text is parsed fresh (watch the numbers change), and the result is
    never cached, so each edit is a real run against the engine.
    """
    if voyage_id not in SAMPLE_VOYAGES:
        return jsonify({"error": "voyage not found"}), 404
    body = request.get_json(silent=True) or {}
    contract_text = body.get("contract_text")
    if not contract_text:
        return jsonify({"error": "contract_text required"}), 400
    try:
        result = audit_voyage(voyage_id, SAMPLE_VOYAGES[voyage_id], contract_override=contract_text)
        result["_cached"] = False
        result["_edited"] = True
        return jsonify(result)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 500


@app.route("/api/cache", methods=["DELETE"])
def clear_cache():
    """Clear all cached audit results."""
    removed = 0
    for fn in os.listdir(CACHE_DIR):
        if fn.endswith(".json"):
            os.remove(os.path.join(CACHE_DIR, fn))
            removed += 1
    return jsonify({"cleared": removed})


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8788"))
    print(f"AnchorClaim API + UI on http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
