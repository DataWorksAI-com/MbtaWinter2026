from datetime import datetime
import json
import os
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from flask import Flask, jsonify, request
from flask_cors import CORS

TEST_MODE = os.getenv("TEST_MODE") == "1"

if not TEST_MODE:
    from pymongo import MongoClient

app = Flask(__name__, static_folder="static")
CORS(app)

DEFAULT_PORT = 6900


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


MONGO_URI = os.getenv("MONGODB_URI") or os.getenv("MONGO_URI")
MONGO_DBNAME = os.getenv("MONGODB_DB", "nanda_private_registry")

ENABLE_FEDERATION = _env_bool("ENABLE_FEDERATION", default=False)
SWITCHBOARD_TIMEOUT_SECONDS = float(os.getenv("SWITCHBOARD_TIMEOUT_SECONDS", "5"))
AGNTCY_ADS_URL = (os.getenv("AGNTCY_ADS_URL") or "").rstrip("/")
AGNTCY_ADS_SEARCH_PATH = os.getenv("AGNTCY_ADS_SEARCH_PATH", "/v1/search")
AGNTCY_ADS_TOKEN = (os.getenv("AGNTCY_ADS_TOKEN") or "").strip()
NEU_REGISTRY_URL = (os.getenv("NEU_REGISTRY_URL") or "").rstrip("/")

ENABLE_EXTERNAL_REGISTRATION = _env_bool("ENABLE_EXTERNAL_REGISTRATION", default=False)
NEU_REGISTRY_REGISTER_URL = (os.getenv("NEU_REGISTRY_REGISTER_URL") or "").rstrip("/")
AGNTCY_REGISTER_WEBHOOK_URL = (os.getenv("AGNTCY_REGISTER_WEBHOOK_URL") or "").rstrip("/")


if not TEST_MODE:
    try:
        mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        mongo_client.admin.command("ping")
        mongo_db = mongo_client[MONGO_DBNAME]
        agent_registry_col = mongo_db.get_collection("agents")
        client_registry_col = mongo_db.get_collection("client_registry")
        users_col = mongo_db.get_collection("users")
        mcp_registry_col = mongo_db.get_collection("mcp_registry")
        messages_col = mongo_db.get_collection("messages")
        USE_MONGO = True
        print("✅ MongoDB connected")
    except Exception as e:
        USE_MONGO = False
        agent_registry_col = None
        client_registry_col = None
        users_col = None
        mcp_registry_col = None
        messages_col = None
        print(f"⚠️  MongoDB unavailable: {e}")
else:
    USE_MONGO = False
    agent_registry_col = None
    client_registry_col = None
    users_col = None
    mcp_registry_col = None
    messages_col = None

registry = {"agent_status": {}}
client_registry = {"agent_map": {}}

if not TEST_MODE and USE_MONGO and agent_registry_col is not None:
    try:
        for doc in agent_registry_col.find():
            agent_id = doc.get("agent_id")
            if not agent_id:
                continue
            registry[agent_id] = doc.get("agent_url")
            registry["agent_status"][agent_id] = {
                "alive": doc.get("alive", False),
                "assigned_to": doc.get("assigned_to"),
                "last_update": doc.get("last_update"),
                "api_url": doc.get("api_url"),
                "description": doc.get("description", ""),
            }
        print(f"📚 Loaded {len(registry) - 1} agents")
    except Exception as e:
        print(f"⚠️  Error loading agents: {e}")

if not TEST_MODE and USE_MONGO and client_registry_col is not None:
    try:
        for doc in client_registry_col.find():
            client_name = doc.get("client_name")
            if not client_name:
                continue
            client_registry[client_name] = doc.get("api_url")
            client_registry["agent_map"][client_name] = doc.get("agent_id")
        print(f"👥 Loaded {len(client_registry) - 1} clients")
    except Exception as e:
        print(f"⚠️  Error loading clients: {e}")


def _http_json(
    url: str,
    method: str = "GET",
    payload: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    body = None
    req_headers = {"Accept": "application/json"}
    if headers:
        req_headers.update(headers)

    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        req_headers["Content-Type"] = "application/json"

    req = Request(url=url, data=body, headers=req_headers, method=method)
    timeout_value = timeout if timeout is not None else SWITCHBOARD_TIMEOUT_SECONDS

    try:
        with urlopen(req, timeout=timeout_value) as resp:
            raw = resp.read().decode("utf-8")
            if not raw:
                return {}
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
            return {"items": data}
    except HTTPError as e:
        if e.code == 404:
            return None
        print(f"⚠️  HTTP error calling {url}: {e}")
        return None
    except (URLError, ValueError, TimeoutError, ConnectionResetError, OSError) as e:
        print(f"⚠️  Request error calling {url}: {e}")
        return None


def _http_probe_json(
    url: str,
    method: str = "GET",
    payload: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: Optional[float] = None,
) -> Dict[str, Any]:
    body = None
    req_headers = {"Accept": "application/json"}
    if headers:
        req_headers.update(headers)

    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        req_headers["Content-Type"] = "application/json"

    req = Request(url=url, data=body, headers=req_headers, method=method)
    timeout_value = timeout if timeout is not None else SWITCHBOARD_TIMEOUT_SECONDS

    try:
        with urlopen(req, timeout=timeout_value) as resp:
            status_code = getattr(resp, "status", 200)
            raw = resp.read().decode("utf-8")
            parsed: Any = None
            if raw:
                try:
                    parsed = json.loads(raw)
                except ValueError:
                    parsed = {"raw": raw}
            return {
                "ok": True,
                "status_code": status_code,
                "error": None,
                "data": parsed,
            }
    except HTTPError as e:
        raw = ""
        try:
            raw = e.read().decode("utf-8")
        except Exception:
            raw = ""
        parsed: Any = None
        if raw:
            try:
                parsed = json.loads(raw)
            except ValueError:
                parsed = {"raw": raw}
        return {
            "ok": False,
            "status_code": e.code,
            "error": str(e),
            "data": parsed,
        }
    except (URLError, ValueError, TimeoutError, ConnectionResetError, OSError) as e:
        return {
            "ok": False,
            "status_code": None,
            "error": str(e),
            "data": None,
        }


def _build_agent_payload(agent_id: str) -> Dict[str, Any]:
    agent_url = registry.get(agent_id)
    status_obj = registry.get("agent_status", {}).get(agent_id, {})
    return {
        "agent_id": agent_id,
        "agent_url": agent_url,
        "api_url": status_obj.get("api_url"),
        "alive": status_obj.get("alive", False),
        "assigned_to": status_obj.get("assigned_to"),
        "last_update": status_obj.get("last_update"),
        "capabilities": status_obj.get("capabilities", []),
        "tags": status_obj.get("tags", []),
        "description": status_obj.get("description", ""),
    }


def _translate_agntcy_record(raw: Dict[str, Any], agent_name: str) -> Dict[str, Any]:
    skills = raw.get("skills") or []
    capabilities: List[str] = []
    for item in skills:
        name = item.get("name") if isinstance(item, dict) else None
        if isinstance(name, str) and name:
            capabilities.append(name.split("/")[-1])

    agent_url = ""
    locators = raw.get("locators") or []
    if isinstance(locators, list):
        for locator in locators:
            if not isinstance(locator, dict):
                continue
            maybe_url = locator.get("url")
            if isinstance(maybe_url, str) and maybe_url:
                agent_url = maybe_url
                break

    return {
        "agent_id": f"@agntcy:{agent_name}",
        "agent_name": agent_name,
        "agent_url": agent_url,
        "api_url": AGNTCY_ADS_URL,
        "description": raw.get("description", ""),
        "capabilities": capabilities,
        "tags": [],
        "alive": True,
        "schema_version": "nanda-v1",
        "source_schema": "oasf",
        "source_registry": "agntcy",
        "raw": raw,
    }


def _query_neu(agent_name: str) -> Optional[Dict[str, Any]]:
    if not NEU_REGISTRY_URL:
        return None

    agent_path = quote(agent_name, safe="")
    direct = _http_json(f"{NEU_REGISTRY_URL}/agents/{agent_path}")
    if direct is None:
        direct = _http_json(f"{NEU_REGISTRY_URL}/lookup/{agent_path}")
    if direct is None:
        return None

    source_id = direct.get("agent_id") or agent_name
    payload = {
        "agent_id": source_id if str(source_id).startswith("@") else f"@neu:{source_id}",
        "agent_name": source_id,
        "agent_url": direct.get("agent_url", ""),
        "api_url": direct.get("api_url", ""),
        "description": direct.get("description", ""),
        "capabilities": direct.get("capabilities", []),
        "tags": direct.get("tags", []),
        "alive": direct.get("alive", False),
        "schema_version": "nanda-v1",
        "source_schema": "nanda",
        "source_registry": "neu",
    }
    return payload


def _query_agntcy(agent_name: str) -> Optional[Dict[str, Any]]:
    if not AGNTCY_ADS_URL:
        return None

    query = urlencode({"name": agent_name})
    url = f"{AGNTCY_ADS_URL}{AGNTCY_ADS_SEARCH_PATH}?{query}"
    headers: Dict[str, str] = {}
    if AGNTCY_ADS_TOKEN:
        headers["Authorization"] = f"Bearer {AGNTCY_ADS_TOKEN}"

    data = _http_json(url, headers=headers)
    if data is None:
        return None

    candidates: List[Dict[str, Any]] = []
    for key in ("records", "results", "items", "agents"):
        value = data.get(key)
        if isinstance(value, list):
            candidates.extend([v for v in value if isinstance(v, dict)])

    if not candidates and isinstance(data.get("record"), dict):
        candidates.append(data["record"])

    if not candidates and data.get("name"):
        candidates.append(data)

    for candidate in candidates:
        name = str(candidate.get("name", "")).strip()
        if not name:
            continue
        if name == agent_name or name.endswith(agent_name):
            return _translate_agntcy_record(candidate, agent_name)

    return None


def _federated_lookup(identifier: str) -> Optional[Dict[str, Any]]:
    if not ENABLE_FEDERATION:
        return None

    if not identifier.startswith("@") or ":" not in identifier:
        return None

    registry_id, agent_name = identifier[1:].split(":", 1)
    if not registry_id or not agent_name:
        return None

    if registry_id == "neu":
        return _query_neu(agent_name)
    if registry_id == "agntcy":
        return _query_agntcy(agent_name)

    return None


def _switchboard_registry_status() -> Dict[str, Any]:
    registries: List[Dict[str, Any]] = [
        {
            "registry_id": "nanda",
            "status": "active",
            "type": "local",
            "registry_url": "http://localhost:6900",
        }
    ]

    if AGNTCY_ADS_URL:
        registries.append(
            {
                "registry_id": "agntcy",
                "status": "active" if ENABLE_FEDERATION else "configured",
                "server_address": AGNTCY_ADS_URL,
                "sdk_available": False,
            }
        )

    if NEU_REGISTRY_URL:
        registries.append(
            {
                "registry_id": "neu",
                "status": "active" if ENABLE_FEDERATION else "configured",
                "type": "northeastern",
                "registry_url": NEU_REGISTRY_URL,
            }
        )

    return {
        "count": len(registries),
        "federation_enabled": ENABLE_FEDERATION,
        "registries": registries,
    }


def _agntcy_candidates_from_data(data: Any) -> List[Dict[str, Any]]:
    if not isinstance(data, dict):
        return []

    candidates: List[Dict[str, Any]] = []
    for key in ("records", "results", "items", "agents"):
        value = data.get(key)
        if isinstance(value, list):
            candidates.extend([v for v in value if isinstance(v, dict)])

    if not candidates and isinstance(data.get("record"), dict):
        candidates.append(data["record"])

    if not candidates and data.get("name"):
        candidates.append(data)

    return candidates


def _diagnose_neu(sample_agent: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "configured": bool(NEU_REGISTRY_URL),
        "registry_url": NEU_REGISTRY_URL,
        "state": "not_configured",
    }
    if not NEU_REGISTRY_URL:
        return out

    health = _http_probe_json(f"{NEU_REGISTRY_URL}/health")
    out["health_probe"] = {
        "ok": health.get("ok", False),
        "status_code": health.get("status_code"),
        "error": health.get("error"),
    }
    if not health.get("ok"):
        out["state"] = "upstream_unavailable"
        return out

    agent_path = quote(sample_agent, safe="")
    by_agent = _http_probe_json(f"{NEU_REGISTRY_URL}/agents/{agent_path}")
    by_lookup = _http_probe_json(f"{NEU_REGISTRY_URL}/lookup/{agent_path}")
    out["sample_agent"] = sample_agent
    out["sample_probe"] = {
        "agents_status": by_agent.get("status_code"),
        "lookup_status": by_lookup.get("status_code"),
    }

    if by_agent.get("ok") or by_lookup.get("ok"):
        out["state"] = "reachable_found"
    elif by_agent.get("status_code") == 404 and by_lookup.get("status_code") == 404:
        out["state"] = "reachable_empty_result"
    else:
        out["state"] = "reachable_schema_mismatch_or_error"
        out["sample_probe"]["agents_error"] = by_agent.get("error")
        out["sample_probe"]["lookup_error"] = by_lookup.get("error")

    return out


def _diagnose_agntcy(sample_agent: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "configured": bool(AGNTCY_ADS_URL),
        "server_address": AGNTCY_ADS_URL,
        "search_path": AGNTCY_ADS_SEARCH_PATH,
        "state": "not_configured",
    }
    if not AGNTCY_ADS_URL:
        return out

    headers: Dict[str, str] = {}
    if AGNTCY_ADS_TOKEN:
        headers["Authorization"] = f"Bearer {AGNTCY_ADS_TOKEN}"

    query = urlencode({"name": sample_agent})
    url = f"{AGNTCY_ADS_URL}{AGNTCY_ADS_SEARCH_PATH}?{query}"
    probe = _http_probe_json(url, headers=headers)
    out["sample_agent"] = sample_agent
    out["sample_probe"] = {
        "status_code": probe.get("status_code"),
        "error": probe.get("error"),
    }

    if not probe.get("ok"):
        out["state"] = "upstream_unavailable"
        return out

    data = probe.get("data")
    candidates = _agntcy_candidates_from_data(data)
    out["sample_probe"]["candidate_count"] = len(candidates)

    if candidates:
        for candidate in candidates:
            name = str(candidate.get("name", "")).strip()
            if name == sample_agent or name.endswith(sample_agent):
                out["state"] = "reachable_found"
                return out
        out["state"] = "reachable_empty_result"
        return out

    if isinstance(data, dict):
        known_shape = any(k in data for k in ("records", "results", "items", "agents", "record", "name"))
        out["state"] = "reachable_empty_result" if known_shape else "reachable_schema_mismatch"
        out["sample_probe"]["top_level_keys"] = list(data.keys())[:20]
        return out

    out["state"] = "reachable_schema_mismatch"
    return out


def _switchboard_diagnostics(sample_agent: str) -> Dict[str, Any]:
    return {
        "federation_enabled": ENABLE_FEDERATION,
        "sample_agent": sample_agent,
        "registries": {
            "nanda": {
                "configured": True,
                "state": "active_local",
            },
            "neu": _diagnose_neu(sample_agent),
            "agntcy": _diagnose_agntcy(sample_agent),
        },
    }


def _mirror_external_registration(agent_payload: Dict[str, Any]) -> Dict[str, Any]:
    result = {"mirrored": False, "targets": []}
    if not ENABLE_EXTERNAL_REGISTRATION:
        return result

    agent_id = agent_payload.get("agent_id")
    if not isinstance(agent_id, str):
        return result

    if NEU_REGISTRY_REGISTER_URL:
        neu_body = {
            "agent_id": agent_id,
            "agent_url": agent_payload.get("agent_url", ""),
            "description": agent_payload.get("description", ""),
        }
        ok = _http_json(NEU_REGISTRY_REGISTER_URL, method="POST", payload=neu_body) is not None
        result["targets"].append({"registry": "neu", "ok": ok})

    if AGNTCY_REGISTER_WEBHOOK_URL:
        agntcy_body = {
            "source": "mbta-winter-2026",
            "agent_id": agent_id,
            "description": agent_payload.get("description", ""),
            "capabilities": agent_payload.get("capabilities", []),
            "agent_url": agent_payload.get("agent_url", ""),
        }
        ok = _http_json(AGNTCY_REGISTER_WEBHOOK_URL, method="POST", payload=agntcy_body) is not None
        result["targets"].append({"registry": "agntcy", "ok": ok})

    result["mirrored"] = any(t["ok"] for t in result["targets"])
    return result


def save_client_registry() -> None:
    if TEST_MODE or not USE_MONGO or client_registry_col is None:
        return
    try:
        for client_name, api_url in client_registry.items():
            if client_name == "agent_map":
                continue
            agent_id = client_registry.get("agent_map", {}).get(client_name)
            client_registry_col.update_one(
                {"client_name": client_name},
                {"$set": {"api_url": api_url, "agent_id": agent_id}},
                upsert=True,
            )
    except Exception as e:
        print(f"⚠️  Error saving clients: {e}")


def save_registry() -> None:
    if TEST_MODE or not USE_MONGO or agent_registry_col is None:
        return
    try:
        for agent_id, agent_url in registry.items():
            if agent_id == "agent_status":
                continue
            status = registry.get("agent_status", {}).get(agent_id, {})
            mongo_doc = {"agent_id": agent_id, "agent_url": agent_url, **status}
            agent_registry_col.update_one(
                {"agent_id": agent_id},
                {"$set": mongo_doc},
                upsert=True,
            )
    except Exception as e:
        print(f"⚠️  Error saving registry: {e}")


@app.route("/health", methods=["GET"])
def health():
    return jsonify(
        {
            "status": "ok",
            "mongo": USE_MONGO and not TEST_MODE,
            "federation_enabled": ENABLE_FEDERATION,
            "federation_targets": {
                "agntcy": bool(AGNTCY_ADS_URL),
                "neu": bool(NEU_REGISTRY_URL),
            },
            "timestamp": datetime.now().isoformat(),
        }
    )


@app.route("/stats", methods=["GET"])
def stats():
    agents = [a for a in registry.keys() if a != "agent_status"]
    total_agents = len(agents)
    alive_agents = 0
    if "agent_status" in registry:
        alive_agents = sum(1 for a in agents if registry["agent_status"].get(a, {}).get("alive"))
    total_clients = len([c for c in client_registry.keys() if c != "agent_map"])
    return jsonify(
        {
            "total_agents": total_agents,
            "alive_agents": alive_agents,
            "total_clients": total_clients,
            "mongodb_enabled": USE_MONGO and not TEST_MODE,
        }
    )


@app.route("/search", methods=["GET"])
def search_agents():
    query = request.args.get("q", "").strip().lower()
    capabilities_filter = request.args.get("capabilities")
    tags_filter = request.args.get("tags")

    capabilities_list = [c.strip() for c in capabilities_filter.split(",")] if capabilities_filter else []
    tags_list = [t.strip() for t in tags_filter.split(",")] if tags_filter else []

    results: List[Dict[str, Any]] = []
    for agent_id in registry.keys():
        if agent_id == "agent_status":
            continue
        if query and query not in agent_id.lower():
            continue

        payload = _build_agent_payload(agent_id)

        if capabilities_list:
            agent_caps = payload.get("capabilities", []) or []
            if not any(c in agent_caps for c in capabilities_list):
                continue

        if tags_list:
            agent_tags = payload.get("tags", []) or []
            if not any(t in agent_tags for t in tags_list):
                continue

        results.append(payload)

    return jsonify(results)


@app.route("/agents/<agent_id>", methods=["GET"])
def get_agent(agent_id):
    if agent_id not in registry or agent_id == "agent_status":
        return jsonify({"error": "Agent not found"}), 404
    return jsonify(_build_agent_payload(agent_id))


@app.route("/agents/<agent_id>", methods=["DELETE"])
def delete_agent(agent_id):
    if agent_id not in registry or agent_id == "agent_status":
        return jsonify({"error": "Agent not found"}), 404

    registry.pop(agent_id, None)
    if "agent_status" in registry:
        registry["agent_status"].pop(agent_id, None)

    to_remove = []
    for client_name, mapped_agent in client_registry.get("agent_map", {}).items():
        if mapped_agent == agent_id:
            to_remove.append(client_name)

    for client_name in to_remove:
        client_registry.pop(client_name, None)
        client_registry.get("agent_map", {}).pop(client_name, None)

    save_registry()
    save_client_registry()

    return jsonify({"status": "deleted", "agent_id": agent_id})


@app.route("/agents/<agent_id>/status", methods=["PUT"])
def update_agent_status(agent_id):
    if agent_id not in registry or agent_id == "agent_status":
        return jsonify({"error": "Agent not found"}), 404

    data = request.json or {}
    status_obj = registry.get("agent_status", {}).get(agent_id, {})

    if "alive" in data:
        status_obj["alive"] = bool(data["alive"])
    if "assigned_to" in data:
        status_obj["assigned_to"] = data["assigned_to"]

    status_obj["last_update"] = datetime.now().isoformat()

    if "capabilities" in data and isinstance(data["capabilities"], list):
        status_obj["capabilities"] = data["capabilities"]
    if "tags" in data and isinstance(data["tags"], list):
        status_obj["tags"] = data["tags"]
    if "description" in data and isinstance(data["description"], str):
        status_obj["description"] = data["description"]

    registry["agent_status"][agent_id] = status_obj
    save_registry()

    return jsonify({"status": "updated", "agent": _build_agent_payload(agent_id)})


@app.route("/register", methods=["POST"])
def register():
    data = request.json
    if not data or "agent_id" not in data or "agent_url" not in data:
        return jsonify({"error": "Missing agent_id or agent_url"}), 400

    agent_id = data["agent_id"]
    agent_url = data["agent_url"]
    api_url = data.get("api_url")
    description = data.get("description", "")

    registry[agent_id] = agent_url

    if "agent_status" not in registry:
        registry["agent_status"] = {}

    registry["agent_status"][agent_id] = {
        "alive": False,
        "assigned_to": None,
        "api_url": api_url,
        "description": description,
        "last_update": datetime.now().isoformat(),
    }

    save_registry()
    print(f"✅ Registered: {agent_id}")

    mirror_result = _mirror_external_registration(
        {
            "agent_id": agent_id,
            "agent_url": agent_url,
            "description": description,
            "capabilities": data.get("capabilities", []),
        }
    )

    return jsonify(
        {
            "status": "success",
            "message": f"Agent {agent_id} registered",
            "external_registration": mirror_result,
        }
    )


@app.route("/lookup/<id>", methods=["GET"])
def lookup(id):
    federated = _federated_lookup(id)
    if federated is not None:
        return jsonify(federated)

    if id in registry and id != "agent_status":
        agent_url = registry[id]
        status_obj = registry["agent_status"].get(id, {})
        api_url = status_obj.get("api_url")
        description = status_obj.get("description", "")
        return jsonify(
            {
                "agent_id": id,
                "agent_url": agent_url,
                "api_url": api_url,
                "description": description,
            }
        )

    if id in client_registry:
        agent_id = client_registry["agent_map"][id]
        agent_url = registry[agent_id]
        api_url = client_registry[id]
        status_obj = registry["agent_status"].get(agent_id, {})
        description = status_obj.get("description", "")
        return jsonify(
            {
                "agent_id": agent_id,
                "agent_url": agent_url,
                "api_url": api_url,
                "description": description,
            }
        )

    return jsonify({"error": f"ID '{id}' not found"}), 404


@app.route("/switchboard/registries", methods=["GET"])
def switchboard_registries():
    return jsonify(_switchboard_registry_status())


@app.route("/switchboard/lookup/<path:identifier>", methods=["GET"])
def switchboard_lookup(identifier):
    payload = _federated_lookup(identifier)
    if payload is None:
        return jsonify({"error": f"ID '{identifier}' not found in federated registries"}), 404
    return jsonify(payload)


@app.route("/switchboard/diagnostics", methods=["GET"])
def switchboard_diagnostics():
    sample_agent = request.args.get("agent", "mbta-alerts").strip() or "mbta-alerts"
    return jsonify(_switchboard_diagnostics(sample_agent))


@app.route("/list", methods=["GET"])
def list_agents():
    result = {k: v for k, v in registry.items() if k != "agent_status"}
    return jsonify(result)


@app.route("/clients", methods=["GET"])
def list_clients():
    result = {k: "alive" for k, _ in client_registry.items() if k != "agent_map"}
    return jsonify(result)


@app.route("/", methods=["GET"])
def dashboard():
    ui_path = os.path.join(os.path.dirname(__file__), "static", "registry-ui.html")
    if os.path.exists(ui_path):
        with open(ui_path, encoding="utf-8") as f:
            from flask import Response

            return Response(f.read(), mimetype="text/html")
    return jsonify({"service": "NANDA Registry", "version": "v3"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", DEFAULT_PORT))
    print(f"🚀 Northeastern Registry v3 on port {port}")
    app.run(host="0.0.0.0", port=port)