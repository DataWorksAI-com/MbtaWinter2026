from flask import Flask, request, jsonify
import os
from datetime import datetime, timedelta
from flask_cors import CORS
from typing import Any, Dict, List
import urllib.request
import urllib.error
import json as _json

def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}

TEST_MODE = os.getenv("TEST_MODE") == "1"

if not TEST_MODE:
    from pymongo import MongoClient

app = Flask(__name__, static_folder="static")
CORS(app)

DEFAULT_PORT = 6900

MONGO_URI = os.getenv("MONGODB_URI") or os.getenv("MONGO_URI")
MONGO_DBNAME = os.getenv("MONGODB_DB", "nanda_private_registry")

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
                "description": doc.get("description", "")
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


def save_client_registry():
    if TEST_MODE or not USE_MONGO or client_registry_col is None:
        return
    try:
        for client_name, api_url in client_registry.items():
            if client_name == 'agent_map':
                continue
            agent_id = client_registry.get('agent_map', {}).get(client_name)
            client_registry_col.update_one(
                {"client_name": client_name},
                {"$set": {"api_url": api_url, "agent_id": agent_id}},
                upsert=True,
            )
    except Exception as e:
        print(f"⚠️  Error saving clients: {e}")


def save_registry():
    if TEST_MODE or not USE_MONGO or agent_registry_col is None:
        return
    try:
        for agent_id, agent_url in registry.items():
            if agent_id == 'agent_status':
                continue
            status = registry.get('agent_status', {}).get(agent_id, {})
            mongo_doc = {
                "agent_id": agent_id,
                "agent_url": agent_url,
                **status
            }
            agent_registry_col.update_one(
                {"agent_id": agent_id},
                {"$set": mongo_doc},
                upsert=True,
            )
    except Exception as e:
        print(f"⚠️  Error saving registry: {e}")


@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        "status": "ok",
        "mongo": USE_MONGO and not TEST_MODE,
        "timestamp": datetime.now().isoformat()
    })


@app.route('/stats', methods=['GET'])
def stats():
    agents = [a for a in registry.keys() if a != 'agent_status']
    total_agents = len(agents)
    alive_agents = 0
    if 'agent_status' in registry:
        alive_agents = sum(1 for a in agents if registry['agent_status'].get(a, {}).get('alive'))
    total_clients = len([c for c in client_registry.keys() if c != 'agent_map'])
    return jsonify({
        'total_agents': total_agents,
        'alive_agents': alive_agents,
        'total_clients': total_clients,
        'mongodb_enabled': USE_MONGO and not TEST_MODE
    })


def _build_agent_payload(agent_id: str) -> Dict[str, Any]:
    agent_url = registry.get(agent_id)
    status_obj = registry.get('agent_status', {}).get(agent_id, {})
    return {
        'agent_id': agent_id,
        'agent_url': agent_url,
        'api_url': status_obj.get('api_url'),
        'alive': status_obj.get('alive', False),
        'assigned_to': status_obj.get('assigned_to'),
        'last_update': status_obj.get('last_update'),
        'capabilities': status_obj.get('capabilities', []),
        'tags': status_obj.get('tags', []),
        'description': status_obj.get('description', '')
    }


@app.route('/search', methods=['GET'])
def search_agents():
    query = request.args.get('q', '').strip().lower()
    capabilities_filter = request.args.get('capabilities')
    tags_filter = request.args.get('tags')

    capabilities_list = [c.strip() for c in capabilities_filter.split(',')] if capabilities_filter else []
    tags_list = [t.strip() for t in tags_filter.split(',')] if tags_filter else []

    results: List[Dict[str, Any]] = []
    for agent_id in registry.keys():
        if agent_id == 'agent_status':
            continue
        if query and query not in agent_id.lower():
            continue

        payload = _build_agent_payload(agent_id)

        if capabilities_list:
            agent_caps = payload.get('capabilities', []) or []
            if not any(c in agent_caps for c in capabilities_list):
                continue

        if tags_list:
            agent_tags = payload.get('tags', []) or []
            if not any(t in agent_tags for t in tags_list):
                continue

        results.append(payload)

    return jsonify(results)


@app.route('/agents/<agent_id>', methods=['GET'])
def get_agent(agent_id):
    if agent_id not in registry or agent_id == 'agent_status':
        return jsonify({'error': 'Agent not found'}), 404
    return jsonify(_build_agent_payload(agent_id))


@app.route('/agents/<agent_id>', methods=['DELETE'])
def delete_agent(agent_id):
    if agent_id not in registry or agent_id == 'agent_status':
        return jsonify({'error': 'Agent not found'}), 404

    registry.pop(agent_id, None)
    if 'agent_status' in registry:
        registry['agent_status'].pop(agent_id, None)

    to_remove = []
    for client_name, mapped_agent in client_registry.get('agent_map', {}).items():
        if mapped_agent == agent_id:
            to_remove.append(client_name)

    for client_name in to_remove:
        client_registry.pop(client_name, None)
        client_registry.get('agent_map', {}).pop(client_name, None)

    save_registry()
    save_client_registry()

    return jsonify({'status': 'deleted', 'agent_id': agent_id})


@app.route('/agents/<agent_id>/status', methods=['PUT'])
def update_agent_status(agent_id):
    if agent_id not in registry or agent_id == 'agent_status':
        return jsonify({'error': 'Agent not found'}), 404

    data = request.json or {}
    status_obj = registry.get('agent_status', {}).get(agent_id, {})

    if 'alive' in data:
        status_obj['alive'] = bool(data['alive'])
    if 'assigned_to' in data:
        status_obj['assigned_to'] = data['assigned_to']

    status_obj['last_update'] = datetime.now().isoformat()

    if 'capabilities' in data and isinstance(data['capabilities'], list):
        status_obj['capabilities'] = data['capabilities']
    if 'tags' in data and isinstance(data['tags'], list):
        status_obj['tags'] = data['tags']
    if 'description' in data and isinstance(data['description'], str):
        status_obj['description'] = data['description']

    registry['agent_status'][agent_id] = status_obj
    save_registry()

    return jsonify({'status': 'updated', 'agent': _build_agent_payload(agent_id)})


@app.route('/register', methods=['POST'])
def register():
    data = request.json
    if not data or 'agent_id' not in data or 'agent_url' not in data:
        return jsonify({"error": "Missing agent_id or agent_url"}), 400

    agent_id = data['agent_id']
    agent_url = data['agent_url']
    api_url = data.get('api_url')
    description = data.get('description', '')

    registry[agent_id] = agent_url

    if 'agent_status' not in registry:
        registry['agent_status'] = {}

    registry['agent_status'][agent_id] = {
        'alive': False,
        'assigned_to': None,
        'api_url': api_url,
        'description': description,
        'last_update': datetime.now().isoformat()
    }

    save_registry()
    print(f"✅ Registered: {agent_id}")

    return jsonify({"status": "success", "message": f"Agent {agent_id} registered"})


@app.route('/lookup/<id>', methods=['GET'])
def lookup(id):
    if id in registry and id != 'agent_status':
        agent_url = registry[id]
        status_obj = registry['agent_status'].get(id, {})
        api_url = status_obj.get('api_url')
        description = status_obj.get('description', '')
        return jsonify({
            "agent_id": id,
            "agent_url": agent_url,
            "api_url": api_url,
            "description": description
        })

    if id in client_registry:
        agent_id = client_registry["agent_map"][id]
        agent_url = registry[agent_id]
        api_url = client_registry[id]
        status_obj = registry['agent_status'].get(agent_id, {})
        description = status_obj.get('description', '')
        return jsonify({
            "agent_id": agent_id,
            "agent_url": agent_url,
            "api_url": api_url,
            "description": description
        })

    return jsonify({"error": f"ID '{id}' not found"}), 404


@app.route('/list', methods=['GET'])
def list_agents():
    result = {k: v for k, v in registry.items() if k != 'agent_status'}
    return jsonify(result)


@app.route('/clients', methods=['GET'])
def list_clients():
    result = {k: 'alive' for k, v in client_registry.items() if k != 'agent_map'}
    return jsonify(result)


# Serve the UI dashboard at root
@app.route('/', methods=['GET'])
def dashboard():
    ui_path = os.path.join(os.path.dirname(__file__), 'static', 'registry-ui.html')
    if os.path.exists(ui_path):
        with open(ui_path) as f:
            from flask import Response
            return Response(f.read(), mimetype='text/html')
    return jsonify({"service": "NANDA Registry", "version": "v3"})

import urllib.request
import urllib.error
import json as _json

_nanda_cache = None
_nanda_cache_time = None
_nanda_cache_ttl = timedelta(minutes=10)

ENABLE_FEDERATION       = _env_bool("ENABLE_FEDERATION", default=False)
USE_SWITCHBOARD         = _env_bool("USE_SWITCHBOARD",   default=False)
SWITCHBOARD_TIMEOUT     = float(os.getenv("SWITCHBOARD_TIMEOUT_SECONDS", "5"))
NEU_REGISTRY_URL        = os.getenv("NEU_REGISTRY_URL",        "").rstrip("/")
AGNTCY_ADS_URL          = os.getenv("AGNTCY_ADS_URL",          "").rstrip("/")
AGNTCY_ADS_SEARCH_PATH  = os.getenv("AGNTCY_ADS_SEARCH_PATH",  "/v1/search")
AGNTCY_ADS_TOKEN        = os.getenv("AGNTCY_ADS_TOKEN",        "").strip()
AGNTCY_ADS_GRPC_ADDRESS = os.getenv("AGNTCY_ADS_GRPC_ADDRESS", "").strip()
MIT_NANDA_URL           = os.getenv("MIT_NANDA_URL",           "").rstrip("/")

def _http_get(url: str, token: str = "") -> dict:
    """Simple blocking HTTP GET, returns parsed JSON or raises."""
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=SWITCHBOARD_TIMEOUT) as resp:
        return _json.loads(resp.read().decode())


def _classify_upstream(name: str, base_url: str, sample_agent: str,
                        search_path: str = "/list", token: str = "") -> dict:
    """
    Probe an upstream registry and classify its state.
    Returns {"registry": name, "url": base_url, "state": <classification>, "detail": ...}
    """
    if not base_url:
        return {"registry": name, "url": base_url, "state": "upstream_unavailable",
                "detail": "URL not configured"}
    try:
        data = _http_get(base_url + search_path, token=token)
    except Exception as exc:
        return {"registry": name, "url": base_url, "state": "upstream_unavailable",
                "detail": str(exc)}

    if not isinstance(data, dict):
        return {"registry": name, "url": base_url, "state": "reachable_schema_mismatch",
                "detail": f"Expected dict, got {type(data).__name__}"}

    if sample_agent in data:
        return {"registry": name, "url": base_url, "state": "reachable_found",
                "detail": f"Agent '{sample_agent}' found"}

    return {"registry": name, "url": base_url, "state": "reachable_empty_result",
            "detail": f"Agent '{sample_agent}' not found"}
    

# ──────────────────────────────────────────────────────────────────────────────
# Switchboard federation — adapters + discovery
# ──────────────────────────────────────────────────────────────────────────────

# ── helpers ───────────────────────────────────────────────────────────────────

def _http_get(url: str, token: str = "") -> dict:
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=SWITCHBOARD_TIMEOUT) as resp:
        return _json.loads(resp.read().decode())


def _to_passport(agent_id: str, agent_url: str, capabilities: list,
                 description: str, source: str) -> dict:
    """Unified AI Agent Digital Passport format."""
    return {
        "agent_id":    agent_id,
        "agent_url":   agent_url,
        "capabilities": capabilities,
        "description": description,
        "source":      source,
        "alive":       True,
    }


# ── NEU adapter (REST) ────────────────────────────────────────────────────────

def _neu_lookup(agent_id: str) -> dict:
    data = _http_get(f"{NEU_REGISTRY_URL}/agents/{agent_id}")
    return _to_passport(
        agent_id    = data.get("agent_id", agent_id),
        agent_url   = data.get("agent_url", ""),
        capabilities= data.get("capabilities", []),
        description = data.get("description", ""),
        source      = "neu",
    )


def _neu_search(query: str) -> list:
    """Direct lookup of known MBTA agents in NEU registry."""
    results = []
    try:
        data = _http_get(f"{NEU_REGISTRY_URL}/agents/mbta-alerts")
        if data and not data.get("error"):
            results.append(_to_passport(
                agent_id    = data.get("agent_id", "mbta-alerts"),
                agent_url   = data.get("agent_url", ""),
                capabilities= data.get("capabilities", []),
                description = data.get("description", ""),
                source      = "neu",
            ))
    except Exception as e:
        print(f"⚠️ NEU lookup error: {e}")
    return results

# ── MIT-NANDA adapter (REST) ──────────────────────────────────────────────────

NANDA_MBTA_AGENTS = ["skill-mbta-stopfinder", "skill-mbta-planner"]

def _nanda_search(query: str) -> list:
    """Direct lookup of known MBTA agents in MIT NANDA."""
    results = []
    for agent_id in NANDA_MBTA_AGENTS:
        if any(x in query.lower() for x in ["mbta", "stop", "plan", "route", "trip"]):
            try:
                data = _http_get(f"{MIT_NANDA_URL}/agents/{agent_id}")
                if data and not data.get("error"):
                    results.append(_to_passport(
                        agent_id    = data.get("id", agent_id),
                        agent_url   = data.get("endpoint", ""),
                        capabilities= data.get("specialties", []),
                        description = data.get("description", ""),
                        source      = "mit-nanda",
                    ))
            except Exception as e:
                print(f"⚠️ NANDA lookup {agent_id}: {e}")
    return results

import threading

def _warm_nanda_cache():
    """Pre-fetch all MIT NANDA agents into cache on startup."""
    try:
        print("🔄 Warming MIT NANDA cache...")
        _nanda_search("mbta")
        print(f"✅ MIT NANDA cache warmed: {len(_nanda_cache or [])} agents")
    except Exception as e:
        print(f"⚠️ Cache warm failed: {e}")

# Start cache warming in background thread
threading.Thread(target=_warm_nanda_cache, daemon=True).start()

def _nanda_lookup(agent_id: str) -> dict:
    data = _http_get(f"{MIT_NANDA_URL}/agents/{agent_id}")
    return _to_passport(
        agent_id    = data.get("id", agent_id),
        agent_url   = data.get("endpoint", ""),
        capabilities= data.get("specialties", []),
        description = data.get("description", ""),
        source      = "mit-nanda",
    )

# ── AGNTCY-ADS adapter (gRPC → passport translation) ─────────────────────────

def _oasf_to_passport(oasf_agent: dict, source: str = "agntcy") -> dict:
    """
    Translate OASF hierarchical skills taxonomy into the unified passport format.
    OASF fields: name, locators[{type, url}], skills[{category, subcategory, name}]
    """
    agent_id = oasf_agent.get("name", "")

    # Extract primary URL from locators
    agent_url = ""
    for loc in oasf_agent.get("locators", []):
        if loc.get("type") in ("url", "http", "https", "endpoint"):
            agent_url = loc.get("url", "")
            break
    if not agent_url and oasf_agent.get("locators"):
        agent_url = oasf_agent["locators"][0].get("url", "")

    # Flatten skills taxonomy → capabilities array
    capabilities = []
    for skill in oasf_agent.get("skills", []):
        parts = [
            skill.get("category", ""),
            skill.get("subcategory", ""),
            skill.get("name", ""),
        ]
        cap = ".".join(p for p in parts if p)
        if cap:
            capabilities.append(cap)

    description = oasf_agent.get("description", "")

    return _to_passport(agent_id, agent_url, capabilities, description, source)


def _agntcy_grpc_search(query: str) -> list:
    if not _AGNTCY_SDK_AVAILABLE or not AGNTCY_ADS_GRPC_ADDRESS:
        return []
    try:
        cfg = _DirConfig(address=AGNTCY_ADS_GRPC_ADDRESS)
        client = _DirClient(cfg)
        req = _search_v1.SearchCIDsRequest(queries=[query], limit=10)
        resp = client.search_cids(req)
        cids = getattr(resp, "cids", [])
        results = []
        for cid in cids:
            try:
                get_req = _core_v1.GetRequest(cid=cid)
                agent = client.get(get_req)
                raw = _json.loads(agent.SerializeToString()) if hasattr(agent, "SerializeToString") else {}
                results.append(_oasf_to_passport(raw))
            except Exception as e:
                print(f"⚠️ AGNTCY get error for CID {cid}: {e}")
        return results
    except Exception as e:
        print(f"⚠️ AGNTCY gRPC search error: {e}")
        return []

def _agntcy_grpc_lookup(agent_id: str) -> dict:
    """Lookup a single agent by ID via AGNTCY ADS gRPC."""
    if not _AGNTCY_SDK_AVAILABLE or not AGNTCY_ADS_GRPC_ADDRESS:
        raise RuntimeError("AGNTCY SDK unavailable or address not configured")
    cfg = _DirConfig(address=AGNTCY_ADS_GRPC_ADDRESS)
    client = _DirClient(cfg)
    req = _core_v1.GetAgentRequest(name=agent_id)
    resp = client.get_agent(req)
    raw = _json.loads(resp.SerializeToString()) if hasattr(resp, "SerializeToString") else {}
    return _oasf_to_passport(raw)


# ── classify helper (for diagnostics) ────────────────────────────────────────

import urllib.parse

def _classify_upstream(name: str, base_url: str, sample_agent: str,
                        search_path: str = "/list", token: str = "") -> dict:
    if not base_url:
        return {"registry": name, "url": base_url,
                "state": "upstream_unavailable", "detail": "URL not configured"}
    try:
        data = _http_get(base_url + search_path, token=token)
    except Exception as exc:
        return {"registry": name, "url": base_url,
                "state": "upstream_unavailable", "detail": str(exc)}
    if not isinstance(data, dict):
        return {"registry": name, "url": base_url,
                "state": "reachable_schema_mismatch",
                "detail": f"Expected dict, got {type(data).__name__}"}
    if sample_agent in data:
        return {"registry": name, "url": base_url,
                "state": "reachable_found", "detail": f"Agent '{sample_agent}' found"}
    return {"registry": name, "url": base_url,
            "state": "reachable_empty_result",
            "detail": f"Agent '{sample_agent}' not found"}


# ── routes ────────────────────────────────────────────────────────────────────

@app.route('/switchboard/registries', methods=['GET'])
def switchboard_registries():
    upstreams = []
    if NEU_REGISTRY_URL:
        try:
            _http_get(NEU_REGISTRY_URL + "/health")
            upstreams.append({"name": "neu", "url": NEU_REGISTRY_URL, "reachable": True})
        except Exception as e:
            upstreams.append({"name": "neu", "url": NEU_REGISTRY_URL,
                              "reachable": False, "error": str(e)})
    if AGNTCY_ADS_GRPC_ADDRESS:
        upstreams.append({
            "name": "agntcy", "url": AGNTCY_ADS_GRPC_ADDRESS,
            "protocol": "grpc",
            "sdk_available": _AGNTCY_SDK_AVAILABLE,
            "reachable": _AGNTCY_SDK_AVAILABLE,
        })
    if MIT_NANDA_URL:
        try:
            _http_get(MIT_NANDA_URL + "/health")
            upstreams.append({"name": "mit-nanda", "url": MIT_NANDA_URL, "reachable": True})
        except Exception as e:
            upstreams.append({"name": "mit-nanda", "url": MIT_NANDA_URL,
                              "reachable": False, "error": str(e)})
    return jsonify({
        "federation_enabled": ENABLE_FEDERATION,
        "use_switchboard":    USE_SWITCHBOARD,
        "local_registry":     "http://registry:6900",
        "upstreams":          upstreams,
    })


@app.route('/switchboard/lookup/<path:switchboard_id>', methods=['GET'])
def switchboard_lookup(switchboard_id):
    if not ENABLE_FEDERATION:
        return jsonify({"error": "Federation is disabled"}), 503
    try:
        if switchboard_id.startswith("@neu:"):
            agent_id = switchboard_id[5:]
            if not NEU_REGISTRY_URL:
                return jsonify({"error": "NEU_REGISTRY_URL not configured"}), 503
            return jsonify({"source": "neu", "agent": _neu_lookup(agent_id)})

        elif switchboard_id.startswith("@nanda:"):
            agent_id = switchboard_id[7:]
            if not MIT_NANDA_URL:
                return jsonify({"error": "MIT_NANDA_URL not configured"}), 503
            return jsonify({"source": "mit-nanda", "agent": _nanda_lookup(agent_id)})

        elif switchboard_id.startswith("@agntcy:"):
            agent_id = switchboard_id[8:]
            return jsonify({"source": "agntcy", "agent": _agntcy_grpc_lookup(agent_id)})

        else:
            return jsonify({"error": f"Unknown prefix in '{switchboard_id}'"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route('/switchboard/discover', methods=['GET'])
def switchboard_discover():
    """
    Capability-based federated discovery across all three registries.
    Returns unified passport list sorted by source.
    Query params:
      q         — capability/keyword search string
      registry  — filter to one registry: neu | agntcy | mit-nanda (optional)
    """
    if not ENABLE_FEDERATION:
        return jsonify({"error": "Federation is disabled"}), 503

    query    = request.args.get("q", "").strip()
    reg_filter = request.args.get("registry", "").strip().lower()
    results  = []
    errors   = {}

    if not reg_filter or reg_filter == "neu":
        if NEU_REGISTRY_URL:
            try:
                results.extend(_neu_search(query))
            except Exception as e:
                errors["neu"] = str(e)

    if not reg_filter or reg_filter == "agntcy":
        if AGNTCY_ADS_GRPC_ADDRESS:
            try:
                results.extend(_agntcy_grpc_search(query))
            except Exception as e:
                errors["agntcy"] = str(e)

    if not reg_filter or reg_filter == "mit-nanda":
        if MIT_NANDA_URL:
            try:
                results.extend(_nanda_search(query))
            except Exception as e:
                errors["mit-nanda"] = str(e)

    return jsonify({
        "query":   query,
        "count":   len(results),
        "agents":  results,
        "errors":  errors,
    })


@app.route('/switchboard/diagnostics', methods=['GET'])
def switchboard_diagnostics():
    sample_agent = request.args.get("agent", "mbta-alerts")
    results = []
    if NEU_REGISTRY_URL:
        results.append(_classify_upstream(
            "neu", NEU_REGISTRY_URL, sample_agent, search_path="/list"))
    if AGNTCY_ADS_GRPC_ADDRESS:
        results.append({
            "registry": "agntcy",
            "url": AGNTCY_ADS_GRPC_ADDRESS,
            "protocol": "grpc",
            "state": "reachable_found" if _AGNTCY_SDK_AVAILABLE else "upstream_unavailable",
            "detail": "gRPC SDK available" if _AGNTCY_SDK_AVAILABLE else "AGNTCY SDK not installed",
        })
    if MIT_NANDA_URL:
        results.append(_classify_upstream(
            "mit-nanda", MIT_NANDA_URL, sample_agent, search_path="/list"))
    return jsonify({
        "federation_enabled": ENABLE_FEDERATION,
        "sample_agent":       sample_agent,
        "diagnostics":        results,
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', DEFAULT_PORT))
    print(f"🚀 Northeastern Registry v3 on port {port}")
    app.run(host='0.0.0.0', port=port)
