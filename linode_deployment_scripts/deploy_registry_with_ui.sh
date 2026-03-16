#!/bin/bash

# Deploy Northeastern Registry v3 with Full Description Support
# Usage: bash deploy_registry_v3.sh <MONGODB_URL> [REGION] [INSTANCE_TYPE]

set -e

MONGODB_URL="$1"
REGION="${2:-us-east}"
INSTANCE_TYPE="${3:-g6-standard-2}"
ROOT_PASSWORD="${4:-}"

if [ -z "$MONGODB_URL" ]; then
    echo "❌ Usage: $0 <MONGODB_URL> [REGION] [INSTANCE_TYPE]"
    echo "   Example: bash deploy_registry_v3.sh 'mongodb+srv://user:pass@cluster.mongodb.net/'"
    exit 1
fi

if [ -z "$ROOT_PASSWORD" ]; then
    ROOT_PASSWORD=$(openssl rand -base64 32 | tr -d "=+/" | cut -c1-25)
    echo "🔑 Generated root password: $ROOT_PASSWORD"
fi

FIREWALL_LABEL="Northeastern-registry-v3"
SSH_KEY_LABEL="Northeastern-registry-v3-key"
IMAGE_ID="linode/ubuntu22.04"
DEPLOYMENT_ID=$(date +%Y%m%d-%H%M%S)

echo "🗄️ Deploying Northeastern Registry v3 (Semantic Discovery Ready)"
echo "================================================================"
echo "Deployment ID: $DEPLOYMENT_ID"
echo ""

echo "[1/6] Checking Linode CLI..."
if ! linode-cli --version >/dev/null 2>&1; then
    echo "❌ Linode CLI not installed"
    exit 1
fi
echo "✅ Linode CLI ready"

echo "[2/6] Setting up firewall..."
FIREWALL_ID=$(linode-cli firewalls list --text --no-headers --format="id,label" | grep "$FIREWALL_LABEL" | cut -f1 || echo "")

INBOUND_RULES='[
    {"protocol": "TCP", "ports": "22", "addresses": {"ipv4": ["0.0.0.0/0"]}, "action": "ACCEPT"},
    {"protocol": "TCP", "ports": "80", "addresses": {"ipv4": ["0.0.0.0/0"]}, "action": "ACCEPT"},
    {"protocol": "TCP", "ports": "6900", "addresses": {"ipv4": ["0.0.0.0/0"]}, "action": "ACCEPT"},
    {"protocol": "TCP", "ports": "8000", "addresses": {"ipv4": ["0.0.0.0/0"]}, "action": "ACCEPT"}
]'

if [ -z "$FIREWALL_ID" ]; then
    linode-cli firewalls create \
        --label "$FIREWALL_LABEL" \
        --rules.inbound_policy DROP \
        --rules.outbound_policy ACCEPT \
        --rules.inbound "$INBOUND_RULES" >/dev/null
    FIREWALL_ID=$(linode-cli firewalls list --text --no-headers --format="id,label" | grep "$FIREWALL_LABEL" | cut -f1)
    echo "✅ Created firewall"
else
    linode-cli firewalls rules-update "$FIREWALL_ID" --inbound "$INBOUND_RULES" >/dev/null 2>&1
    echo "✅ Using existing firewall"
fi

echo "[3/6] Setting up SSH key..."
if [ ! -f "${SSH_KEY_LABEL}.pub" ]; then
    ssh-keygen -t rsa -b 4096 -f "$SSH_KEY_LABEL" -N "" -C "registry-$DEPLOYMENT_ID" >/dev/null 2>&1
fi
echo "✅ SSH key ready"

echo "[4/6] Launching Linode..."
INSTANCE_ID=$(linode-cli linodes create \
    --type "$INSTANCE_TYPE" \
    --region "$REGION" \
    --image "$IMAGE_ID" \
    --label "Northeastern-registry-v3-$DEPLOYMENT_ID" \
    --tags "Northeastern-Registry,v3,semantic" \
    --root_pass "$ROOT_PASSWORD" \
    --authorized_keys "$(cat ${SSH_KEY_LABEL}.pub)" \
    --firewall_id "$FIREWALL_ID" \
    --text --no-headers --format="id")
echo "✅ Instance ID: $INSTANCE_ID"

echo "   Waiting for instance..."
while true; do
    STATUS=$(linode-cli linodes view "$INSTANCE_ID" --text --no-headers --format="status")
    [ "$STATUS" = "running" ] && break
    sleep 10
done

PUBLIC_IP=$(linode-cli linodes view "$INSTANCE_ID" --text --no-headers --format="ipv4")
echo "✅ Public IP: $PUBLIC_IP"

echo "[5/6] Waiting for SSH..."
for i in {1..30}; do
    if ssh -i "$SSH_KEY_LABEL" -o StrictHostKeyChecking=no -o ConnectTimeout=5 "root@$PUBLIC_IP" "echo ready" >/dev/null 2>&1; then
        echo "✅ SSH ready"
        break
    fi
    sleep 10
done

echo "[6/6] Setting up registry + UI with description support..."

ssh -i "$SSH_KEY_LABEL" -o StrictHostKeyChecking=no "root@$PUBLIC_IP" bash << REMOTE_SETUP
set -e
exec > /var/log/registry-setup.log 2>&1

echo "=== Setup Started ==="
date

apt-get update -y
apt-get install -y python3 python3-venv python3-pip supervisor nginx

if ! id -u ubuntu >/dev/null 2>&1; then
    useradd -m -s /bin/bash ubuntu
    mkdir -p /home/ubuntu/.ssh
    cp /root/.ssh/authorized_keys /home/ubuntu/.ssh/authorized_keys 2>/dev/null || true
    chown -R ubuntu:ubuntu /home/ubuntu/.ssh
    chmod 700 /home/ubuntu/.ssh
    chmod 600 /home/ubuntu/.ssh/authorized_keys 2>/dev/null || true
fi

cd /home/ubuntu
sudo -u ubuntu mkdir -p Northeastern-registry
cd Northeastern-registry

sudo -u ubuntu python3 -m venv .venv
sudo -u ubuntu bash -c "source .venv/bin/activate && pip install --upgrade pip && pip install flask flask-cors pymongo"

cat > registry.py << 'REGISTRY_EOF'
from flask import Flask, request, jsonify
import os
from datetime import datetime
from flask_cors import CORS
from typing import Any, Dict, List

TEST_MODE = os.getenv("TEST_MODE") == "1"

if not TEST_MODE:
    from pymongo import MongoClient

app = Flask(__name__)
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

if __name__ == '__main__':
    port = int(os.environ.get('PORT', DEFAULT_PORT))
    print(f"🚀 Northeastern Registry v3 on port {port}")
    app.run(host='0.0.0.0', port=port)
REGISTRY_EOF

cat > agentFactsServer.py << 'FACTS_EOF'
from flask import Flask, request, jsonify
from pymongo import MongoClient
import os

app = Flask(__name__)

ATLAS_URL = os.getenv("ATLAS_URL")
client = MongoClient(ATLAS_URL)
db = client.nanda_private_registry
facts = db.agent_facts

try:
    facts.create_index("agent_name", unique=True)
except:
    pass

@app.post("/api/agent-facts")
def create_agent_facts():
    agent_facts = request.json
    try:
        result = facts.insert_one(agent_facts)
        return jsonify({"status": "success", "id": str(result.inserted_id)})
    except Exception as e:
        if "duplicate" in str(e):
            agent_name = agent_facts.get("agent_name")
            facts.update_one({"agent_name": agent_name}, {"\\$set": agent_facts})
            return jsonify({"status": "success", "message": "updated"})
        return jsonify({"error": str(e)}), 500

@app.get("/@<username>.json")
def get_agent_facts(username):
    fact = facts.find_one({"agent_name": username}, {"_id": 0})
    if not fact:
        return jsonify({"error": "Not found"}), 404
    return jsonify(fact)

@app.get("/list")
def list_agent_facts():
    all_facts = list(facts.find({}, {"_id": 0}))
    return jsonify({"agent_facts": all_facts, "count": len(all_facts)})

@app.get("/health")
def health_check():
    try:
        client.admin.command('ping')
        return jsonify({"status": "healthy", "mongodb": "connected"})
    except Exception as e:
        return jsonify({"status": "unhealthy", "error": str(e)})

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 8000))
    app.run(host="0.0.0.0", port=port)
FACTS_EOF

cat > registry-ui.html << 'UI_EOF'
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Northeastern Registry v3 - Semantic Discovery</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        .container { max-width: 1400px; margin: 0 auto; }
        .header {
            background: white;
            padding: 30px;
            border-radius: 15px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
            margin-bottom: 30px;
        }
        .header h1 { color: #667eea; font-size: 2.5em; margin-bottom: 10px; }
        .header .subtitle { color: #666; font-size: 1.1em; }
        .stats {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        .stat-card {
            background: white;
            padding: 25px;
            border-radius: 15px;
            box-shadow: 0 5px 15px rgba(0,0,0,0.1);
        }
        .stat-card h3 { color: #667eea; font-size: 2em; margin-bottom: 5px; }
        .stat-card p { color: #888; font-size: 0.9em; }
        .controls {
            background: white;
            padding: 25px;
            border-radius: 15px;
            margin-bottom: 30px;
            display: flex;
            gap: 15px;
            flex-wrap: wrap;
        }
        .controls input { 
            flex: 1; 
            min-width: 200px; 
            padding: 12px; 
            border: 2px solid #e0e0e0; 
            border-radius: 10px;
            font-size: 1em;
        }
        .btn {
            padding: 12px 25px;
            border: none;
            border-radius: 10px;
            cursor: pointer;
            font-weight: 600;
            background: #667eea;
            color: white;
            font-size: 1em;
        }
        .btn:hover { background: #5568d3; }
        .agents-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(400px, 1fr));
            gap: 20px;
        }
        .agent-card {
            background: white;
            border-radius: 15px;
            padding: 25px;
            box-shadow: 0 5px 15px rgba(0,0,0,0.1);
            transition: all 0.3s;
        }
        .agent-card:hover { 
            transform: translateY(-5px);
            box-shadow: 0 8px 25px rgba(0,0,0,0.15);
        }
        .agent-id { 
            font-size: 1.3em; 
            font-weight: 700; 
            color: #667eea; 
            margin-bottom: 15px; 
        }
        .agent-description { 
            font-size: 0.95em; 
            color: #555; 
            margin: 15px 0; 
            line-height: 1.6;
            font-style: italic;
            padding: 12px;
            background: #f8f9fa;
            border-left: 3px solid #667eea;
            border-radius: 5px;
        }
        .status-badge { 
            padding: 6px 14px; 
            border-radius: 20px; 
            font-size: 0.85em; 
            font-weight: 600;
            display: inline-block;
            margin-bottom: 10px;
        }
        .status-alive { background: #d4edda; color: #155724; }
        .status-offline { background: #f8d7da; color: #721c24; }
        .info-row { 
            margin: 10px 0; 
            color: #666; 
            font-size: 0.9em;
        }
        .info-row strong { color: #333; }
        .capability-tag {
            background: #e7f3ff;
            color: #0066cc;
            padding: 6px 12px;
            border-radius: 15px;
            font-size: 0.8em;
            display: inline-block;
            margin: 4px 4px 4px 0;
            font-weight: 500;
        }
        .tag-badge {
            background: #fff3cd;
            color: #856404;
            padding: 6px 12px;
            border-radius: 15px;
            font-size: 0.8em;
            display: inline-block;
            margin: 4px 4px 4px 0;
        }
        .loading { 
            text-align: center; 
            padding: 50px;
            color: white;
            font-size: 1.2em;
        }
        .empty-state { 
            text-align: center; 
            padding: 60px; 
            background: white; 
            border-radius: 15px;
        }
        .empty-state h2 { color: #667eea; margin-bottom: 10px; }
        .empty-state p { color: #888; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🗄️ Northeastern Registry v3</h1>
            <p class="subtitle">Semantic Agent Discovery Platform</p>
        </div>
        
        <div class="stats">
            <div class="stat-card">
                <h3 id="totalAgents">-</h3>
                <p>Total Agents</p>
            </div>
            <div class="stat-card">
                <h3 id="aliveAgents">-</h3>
                <p>Alive Agents</p>
            </div>
            <div class="stat-card">
                <h3 id="totalClients">-</h3>
                <p>Total Clients</p>
            </div>
            <div class="stat-card">
                <h3 id="mongoStatus">-</h3>
                <p>MongoDB</p>
            </div>
        </div>
        
        <div class="controls">
            <input type="text" id="searchInput" placeholder="Search agents by name or capability..." />
            <button class="btn" onclick="searchAgents()">🔍 Search</button>
            <button class="btn" onclick="loadAgents()">🔄 Refresh</button>
        </div>
        
        <div id="agentsContainer">
            <div class="loading">Loading agents...</div>
        </div>
    </div>
    
    <script>
        const REGISTRY_URL = \`http://\${window.location.hostname}:6900\`;
        
        document.addEventListener('DOMContentLoaded', loadDashboard);
        
        async function loadDashboard() {
            await loadStats();
            await loadAgents();
        }
        
        async function loadStats() {
            try {
                const res = await fetch(\`\${REGISTRY_URL}/stats\`);
                const stats = await res.json();
                document.getElementById('totalAgents').textContent = stats.total_agents;
                document.getElementById('aliveAgents').textContent = stats.alive_agents;
                document.getElementById('totalClients').textContent = stats.total_clients;
                document.getElementById('mongoStatus').textContent = stats.mongodb_enabled ? '✅' : '❌';
            } catch (e) {
                console.error('Failed to load stats:', e);
            }
        }
        
        async function loadAgents() {
            const container = document.getElementById('agentsContainer');
            container.innerHTML = '<div class="loading">Loading agents...</div>';
            
            try {
                const res = await fetch(\`\${REGISTRY_URL}/list\`);
                const data = await res.json();
                const ids = Object.keys(data).filter(id => id !== 'agent_status');
                
                if (ids.length === 0) {
                    container.innerHTML = \`
                        <div class="empty-state">
                            <h2>No Agents Registered</h2>
                            <p>Register agents using the /register API endpoint</p>
                        </div>
                    \`;
                    return;
                }
                
                const agents = await Promise.all(ids.map(async id => {
                    try {
                        const r = await fetch(\`\${REGISTRY_URL}/agents/\${id}\`);
                        return r.ok ? await r.json() : null;
                    } catch (e) {
                        console.error(\`Failed to fetch agent \${id}:\`, e);
                        return null;
                    }
                }));
                
                displayAgents(agents.filter(a => a));
            } catch (e) {
                console.error('Failed to load agents:', e);
                container.innerHTML = \`
                    <div class="empty-state">
                        <h2>Error Loading Agents</h2>
                        <p>\${e.message}</p>
                    </div>
                \`;
            }
        }
        
        function displayAgents(agents) {
            const container = document.getElementById('agentsContainer');
            
            if (!agents || agents.length === 0) {
                container.innerHTML = \`
                    <div class="empty-state">
                        <h2>No Agents Found</h2>
                    </div>
                \`;
                return;
            }
            
            container.innerHTML = '<div class="agents-grid">' + agents.map(a => \`
                <div class="agent-card">
                    <div class="agent-id">\${a.agent_id}</div>
                    <span class="status-badge \${a.alive ? 'status-alive' : 'status-offline'}">
                        \${a.alive ? '✅ Alive' : '❌ Offline'}
                    </span>
                    
                    \${a.description && a.description.trim() !== '' ? 
                        '<div class="agent-description">"' + a.description + '"</div>' : 
                        '<div class="agent-description" style="opacity: 0.5;">No description provided</div>'
                    }
                    
                    <div class="info-row">
                        <strong>Endpoint:</strong> \${a.agent_url || 'N/A'}
                    </div>
                    
                    \${a.api_url ? 
                        '<div class="info-row"><strong>API:</strong> ' + a.api_url + '</div>' : ''
                    }
                    
                    <div class="info-row" style="font-size: 0.8em; color: #999;">
                        Updated: \${a.last_update ? new Date(a.last_update).toLocaleString() : 'Never'}
                    </div>
                    
                    \${(a.capabilities || []).length > 0 ? 
                        '<div style="margin-top: 15px;">' + 
                        (a.capabilities || []).map(c => \`<span class="capability-tag">\${c}</span>\`).join('') + 
                        '</div>' : ''
                    }
                    
                    \${(a.tags || []).length > 0 ? 
                        '<div style="margin-top: 10px;">' + 
                        (a.tags || []).map(t => \`<span class="tag-badge">\${t}</span>\`).join('') + 
                        '</div>' : ''
                    }
                </div>
            \`).join('') + '</div>';
        }
        
        async function searchAgents() {
            const query = document.getElementById('searchInput').value.trim();
            if (!query) { 
                loadAgents(); 
                return; 
            }
            
            const container = document.getElementById('agentsContainer');
            container.innerHTML = '<div class="loading">Searching...</div>';
            
            try {
                const res = await fetch(\`\${REGISTRY_URL}/search?q=\${encodeURIComponent(query)}\`);
                const agents = await res.json();
                displayAgents(agents);
            } catch (e) {
                console.error('Search failed:', e);
                container.innerHTML = \`
                    <div class="empty-state">
                        <h2>Search Error</h2>
                        <p>\${e.message}</p>
                    </div>
                \`;
            }
        }
        
        // Allow Enter key in search box
        document.addEventListener('DOMContentLoaded', () => {
            document.getElementById('searchInput').addEventListener('keypress', (e) => {
                if (e.key === 'Enter') {
                    searchAgents();
                }
            });
        });
    </script>
</body>
</html>
UI_EOF

chown -R ubuntu:ubuntu /home/ubuntu/Northeastern-registry

cat > /etc/supervisor/conf.d/registry.conf << SUPEOF
[program:registry]
command=/home/ubuntu/Northeastern-registry/.venv/bin/python registry.py
directory=/home/ubuntu/Northeastern-registry
user=ubuntu
autostart=true
autorestart=true
stderr_logfile=/var/log/registry.err.log
stdout_logfile=/var/log/registry.out.log
environment=MONGODB_URI="$MONGODB_URL",MONGODB_DB="nanda_private_registry",PORT="6900"
SUPEOF

cat > /etc/supervisor/conf.d/agentfacts.conf << SUPEOF2
[program:agentfacts]
command=/home/ubuntu/Northeastern-registry/.venv/bin/python agentFactsServer.py
directory=/home/ubuntu/Northeastern-registry
user=ubuntu
autostart=true
autorestart=true
stderr_logfile=/var/log/agentfacts.err.log
stdout_logfile=/var/log/agentfacts.out.log
environment=ATLAS_URL="$MONGODB_URL",PORT="8000"
SUPEOF2

cat > /etc/nginx/sites-available/registry << 'NGINX_EOF'
server {
    listen 80;
    server_name _;
    
    location / {
        root /home/ubuntu/Northeastern-registry;
        index registry-ui.html;
    }
    
    location ~ ^/(health|stats|list|register|search|lookup|agents|clients|mcp_servers) {
        proxy_pass http://127.0.0.1:6900;
        proxy_set_header Host \\$host;
        proxy_set_header X-Real-IP \\$remote_addr;
        add_header Access-Control-Allow-Origin * always;
        add_header Access-Control-Allow-Methods "GET, POST, PUT, DELETE, OPTIONS" always;
    }
}
NGINX_EOF

ln -sf /etc/nginx/sites-available/registry /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default

chmod 755 /home/ubuntu
chmod 755 /home/ubuntu/Northeastern-registry
chmod 644 /home/ubuntu/Northeastern-registry/registry-ui.html

systemctl enable supervisor nginx
systemctl start supervisor nginx
supervisorctl reread
supervisorctl update
nginx -t && systemctl restart nginx

sleep 10

echo "=== Setup Complete ==="
supervisorctl status
echo ""
REMOTE_SETUP

echo ""
echo "Verifying services..."
sleep 5

REGISTRY_STATUS=\$(ssh -i "$SSH_KEY_LABEL" "root@$PUBLIC_IP" "supervisorctl status registry | grep RUNNING" || echo "FAILED")
NGINX_STATUS=\$(ssh -i "$SSH_KEY_LABEL" "root@$PUBLIC_IP" "systemctl is-active nginx" || echo "FAILED")

if [[ "$REGISTRY_STATUS" == "FAILED" ]] || [[ "$NGINX_STATUS" != "active" ]]; then
    echo "⚠️  Warning: Some services may not have started"
    echo "📋 Check logs: ssh -i $SSH_KEY_LABEL root@$PUBLIC_IP 'tail -50 /var/log/registry.err.log'"
else
    echo "✅ All services running"
fi

echo ""
echo "Testing MongoDB connection..."
MONGO_STATUS=\$(ssh -i "$SSH_KEY_LABEL" "root@$PUBLIC_IP" "curl -s http://localhost:6900/health | grep -o '\"mongo\":[^,]*'" || echo "failed")
if [[ "$MONGO_STATUS" == *"true"* ]]; then
    echo "✅ MongoDB connected successfully"
else
    echo "⚠️  MongoDB not connected"
    echo "   Please whitelist IP in MongoDB Atlas Network Access: $PUBLIC_IP"
    echo "   Go to: https://cloud.mongodb.com → Network Access → Add IP Address"
fi

echo ""
echo "🎉 ================================================================"
echo "🎉 Northeastern Registry v3 Deployed!"
echo "🎉 ================================================================"
echo ""
echo "📦 Instance Details:"
echo "   Instance ID: $INSTANCE_ID"
echo "   Public IP: $PUBLIC_IP"
echo "   Root Password: $ROOT_PASSWORD"
echo ""
echo "🌐 Access URLs:"
echo "   Dashboard:  http://$PUBLIC_IP"
echo "   Registry:   http://$PUBLIC_IP:6900"
echo "   Health:     http://$PUBLIC_IP:6900/health"
echo "   Stats:      http://$PUBLIC_IP:6900/stats"
echo ""
echo "🔑 SSH Access:"
echo "   ssh -i $SSH_KEY_LABEL root@$PUBLIC_IP"
echo ""
echo "✨ Features:"
echo "   • Full description support for semantic discovery"
echo "   • MongoDB persistence (if configured)"
echo "   • Real-time dashboard"
echo "   • RESTful API"
echo ""
echo "📚 Example: Register Agent with Description"
echo "   curl -X POST http://$PUBLIC_IP:6900/register \\"
echo "     -H 'Content-Type: application/json' \\"
echo "     -d '{"
echo "       \"agent_id\": \"my-agent\","
echo "       \"agent_url\": \"http://server:8000\","
echo "       \"description\": \"Does amazing things with AI\""
echo "     }'"
echo ""
echo "   curl -X PUT http://$PUBLIC_IP:6900/agents/my-agent/status \\"
echo "     -H 'Content-Type: application/json' \\"
echo "     -d '{"
echo "       \"alive\": true,"
echo "       \"capabilities\": [\"capability1\", \"capability2\"]"
echo "     }'"
echo ""
echo "🛑 Delete Instance:"
echo "   linode-cli linodes delete $INSTANCE_ID"
echo ""
echo "================================================================"
