# MBTA Transit Conversational Intelligence application
## Hybrid Protocol Transit Intelligence System
A Distributed multi-agent system serving Boston's MBTA network with intelligent MCP+A2A protocol orchestration via SLIM transport and NANDA Registry Federation

<img width="1147" height="796" alt="image" src="https://github.com/user-attachments/assets/e04f7bec-f0ec-476f-8426-8012c2aa757c" />

" />

---

## 🎯 **What This Is**

MBTA Transit Conversational Intelligence application demonstrates **hybrid protocol orchestration** by combining:
- **Anthropic's MCP** (Model Context Protocol) for fast, single-tool queries (~400ms)
- **NANDA/Google's A2A** (Agent-to-Agent) for complex multi-agent coordination (~1500ms)
- **Cisco's SLIM** (Semantic Language Interface for Multi-agent) transport for standardized A2A communication
- **Intelligent LLM routing** that achieves **25x performance improvement** for simple queries

---

## 🏗️ **System Architecture**

```
┌─────────────────┐     ┌──────────────────┐     ┌────────────────┐
│  Frontend UI    │────▶│ Exchange Agent   │────▶│ NANDA Registry │
│  (Port 3000)    │     │ (Port 8100)      │     │ (Port 6900)    │
│                 │     │                  │     │                │
│ WebSocket Chat  │     │ Protocol Router  │     │ Agent Discovery│
└─────────────────┘     └────┬─────────┬───┘     └────────────────┘
                             │         │
                    ┌────────┘         └────────┐
                    ▼                           ▼
            ┌──────────────┐          ┌─────────────────────┐
            │  MCP Client  │          │ SLIM A2A Transport  │
            │  (stdio)     │          │ (gRPC on ports      │
            │              │          │  50051-50053)       │
            │ 32 MBTA Tools│          │                     │
            │ 400ms resp.  │          │ ┌─────────────────┐ │
            └──────────────┘          │ │ A2A Agents      │ │
                                      │ │ • Alerts        │ │
                                      │ │ • Planner       │ │
                                      │ │ • StopFinder    │ │
                                      │ │ (Port 8001-8003)│ │
                                      │ └─────────────────┘ │
                                      └─────────────────────┘
                                      
┌─────────────────────────────────────────────────────────┐
│  Observability Stack                                    │
│  • Jaeger (16686)  • Grafana (3001)                     │
│  • ClickHouse (8123)  • OTEL Collector (4317)           │
└─────────────────────────────────────────────────────────┘
```

**4-Server Deployment:**
1. **Exchange Server** - Protocol gateway, intelligent routing, frontend UI
2. **Agents Server** - 3 specialized A2A agents (SLIM enabled)
3. **Registry Server** - NANDA agent discovery and semantic agent lookup
4. **Observability Server** - Distributed tracing, metrics, analytics

---

## 🚀 **Quick Start - Complete Deployment**

### Prerequisites

1. **Linode CLI** (for cloud deployment)
```bash
pip install linode-cli
linode-cli configure
```

2. **API Keys**
- MBTA API Key: Get from https://api-v3.mbta.com/
- OpenAI API Key: Get from https://platform.openai.com/

3. **Time Required**
- Total: ~40 minutes for all 4 servers
- Per server: ~8-10 minutes

---

## 📝 **Step-by-Step Deployment**

### Step 1: Deploy NANDA Registry (8-10 min)

```bash
bash deploy_registry_with_ui.sh
```

**What it does:**
- Creates Linode instance
- Installs MongoDB
- Deploys registry API (port 6900)
- Deploys registry UI dashboard
- Enables semantic agent discovery

**Save this info from output:**
```
Registry IP: XXX.XXX.XXX.XXX
Registry URL: http://XXX.XXX.XXX.XXX:6900
Dashboard: http://XXX.XXX.XXX.XXX
```

---

### Step 2: Deploy MBTA Agents with SLIM Transport (8-10 min)

```bash
bash deploy-agents-only.sh "YOUR_MBTA_API_KEY" "YOUR_OPENAI_API_KEY"
```

**Replace:**
- `YOUR_MBTA_API_KEY` with your actual key (e.g., `c845eff5ae504179bc9cfa69914059de`)
- `YOUR_OPENAI_API_KEY` with your actual key (e.g., `sk-proj-...`)

**What it does:**
- Creates Linode instance
- Installs agntcy-app-sdk and a2a-sdk (SLIM support)
- Deploys 3 A2A agents with SLIM servers:
  - Alerts Agent (HTTP on 8001 + SLIM on 50051)
  - Planner Agent (HTTP on 8002 + SLIM on 50052)
  - StopFinder Agent (HTTP on 8003 + SLIM on 50053)
- Configures both HTTP (fallback) and SLIM (primary) transports

**Save this info from output:**
```
Agents IP: XXX.XXX.XXX.XXX
Password: <save this>
SSH Key: mbta-agents-key

SLIM Ports: 50051, 50052, 50053
HTTP Ports: 8001, 8002, 8003 (fallback)
```

---

### Step 3: Deploy Exchange + Frontend (8-10 min)

```bash
bash deploy-exchange-only.sh "YOUR_OPENAI_API_KEY" "YOUR_MBTA_API_KEY" "AGENTS_IP"
```

**Replace:**
- `YOUR_OPENAI_API_KEY` with your key
- `YOUR_MBTA_API_KEY` with your key
- `AGENTS_IP` with IP from Step 2 (e.g., `96.126.111.107`)

**What it does:**
- Creates Linode instance
- Installs agntcy-app-sdk (SLIM client support)
- Deploys Exchange Agent (port 8100)
  - Intelligent routing (MCP vs A2A)
  - SLIM client for calling agents
  - Query decomposition for multi-agent coordination
- Deploys Frontend UI (port 3000)
- Connects to agents server via SLIM transport

**Save this info from output:**
```
Exchange IP: XXX.XXX.XXX.XXX
Password: <save this>
SSH Key: mbta-exchange-key

USE_SLIM=true (environment variable set)
```

---

### Step 4: Deploy Observability Stack (8-10 min)

```bash
bash deploy-observability.sh
```

**What it does:**
- Creates Linode instance
- Deploys via Docker:
  - Jaeger UI (port 16686)
  - Grafana (port 3001)
  - ClickHouse (port 8123)
  - OTEL Collector (port 4317)
- Configures SLIM trace export (OTLP gRPC)

**Save this info from output:**
```
Observability IP: XXX.XXX.XXX.XXX
Password: <save this>
SSH Key: mbta-observability-key

OTEL gRPC: 4317
OTEL HTTP: 4318
```

**Configure Exchange & Agents to send traces:**

```bash
# SSH to Exchange server
ssh -i mbta-exchange-key root@EXCHANGE_IP

# Add observability endpoint
echo "OTEL_ENDPOINT=http://OBSERVABILITY_IP:4317" >> /opt/mbta-agentcy/.env

# Restart services
supervisorctl restart all
exit

# SSH to Agents server
ssh -i mbta-agents-key root@AGENTS_IP

# Add observability endpoint
echo "OTEL_ENDPOINT=http://OBSERVABILITY_IP:4317" >> /opt/mbta-agents/.env

# Restart services
supervisorctl restart all
exit
```

---

### Step 5: Register Agents in NANDA Registry (1 min)

```bash
# Edit register_agents.sh first
# Replace IP addresses with your actual IPs:
# REGISTRY_URL="http://YOUR_REGISTRY_IP:6900"
# agent_url: "http://YOUR_AGENTS_IP:8001" (HTTP endpoint for registry)

# Then run:
bash register_agents.sh
```

**What it does:**
- Registers 3 agents with semantic descriptions
- Enables dynamic agent discovery via NANDA registry
- Exchange Agent discovers agents at runtime (not hardcoded)
- Sets agent status to "alive"

---

## ✅ **Verify Everything Works**

### Test 1: Check All Services

```bash
# Registry
curl http://REGISTRY_IP:6900/health

# Agents (HTTP endpoints)
curl http://AGENTS_IP:8001/health
curl http://AGENTS_IP:8002/health
curl http://AGENTS_IP:8003/health

# Exchange
curl http://EXCHANGE_IP:8100/

# Observability
curl http://OBSERVABILITY_IP:16686
```

**All should return healthy status!**

---

### Test 2: Verify SLIM Agents Running

```bash
# SSH to agents server
ssh -i mbta-agents-key root@AGENTS_IP

# Check SLIM services
sudo supervisorctl status | grep slim

# Should show:
# mbta-alerts-slim    RUNNING
# mbta-planner-slim   RUNNING
# mbta-stopfinder-slim RUNNING
```

---

### Test 3: Test SLIM Transport

```bash
# Check if SLIM agents are responding
curl http://AGENTS_IP:50051/.well-known/agent-card.json
curl http://AGENTS_IP:50052/.well-known/agent-card.json
curl http://AGENTS_IP:50053/.well-known/agent-card.json

# Should return AgentCard JSON with capabilities
```

---

### Test 4: Send a Query (MCP Fast Path)

```bash
curl -X POST http://EXCHANGE_IP:8100/chat \
  -H "Content-Type: application/json" \
  -d '{"query": "Red Line delays?"}'
```

**Expected response:**
```json
{
  "response": "Good news! There are currently no service alerts...",
  "path": "mcp",
  "latency_ms": 400,
  "intent": "alerts",
  "confidence": 0.95,
  "metadata": {
    "tools_used": ["mbta_get_alerts"]
  }
}
```

---

### Test 5: Send Complex Query (A2A Path via SLIM)

```bash
curl -X POST http://EXCHANGE_IP:8100/chat \
  -H "Content-Type: application/json" \
  -d '{"query": "How do I get from Harvard to MIT?"}'
```

**Expected response:**
```json
{
  "response": "Take the Red Line from Harvard to Kendall/MIT...",
  "path": "a2a",
  "latency_ms": 1500,
  "intent": "trip_planning",
  "confidence": 0.95,
  "metadata": {
    "agents_called": ["mbta-route-planner"],
    "transport": "slim"
  }
}
```

**The agents_called field shows SLIM was used to communicate.**

---

### Test 6: Open Web UI

```
http://EXCHANGE_IP:3000
```

**You should see:**
- Chat interface
- Real-time responses
- System internals panel showing routing decisions and transport used

---

### Test 7: View Distributed Traces (Including SLIM Spans)

```
http://OBSERVABILITY_IP:16686
```

**In Jaeger UI:**
1. Select service: `exchange-agent`
2. Click "Find Traces"
3. Open an A2A trace (1500ms+)
4. You should see spans including:
   - `slim_client.call_agent` (SLIM transport timing)
   - `agent_response_extraction` (parsing response)
   - Individual agent processing times

---

## 📊 **System URLs Reference**

After deployment, save these URLs:

```
Frontend UI:        http://EXCHANGE_IP:3000
Exchange API:       http://EXCHANGE_IP:8100

MBTA Agents (HTTP):
  Alerts:           http://AGENTS_IP:8001
  Planner:          http://AGENTS_IP:8002
  StopFinder:       http://AGENTS_IP:8003

MBTA Agents (SLIM):
  Alerts:           grpc://AGENTS_IP:50051
  Planner:          grpc://AGENTS_IP:50052
  StopFinder:       grpc://AGENTS_IP:50053

NANDA Registry:
  Dashboard:        http://REGISTRY_IP
  API:              http://REGISTRY_IP:6900

Observability:
  Jaeger UI:        http://OBSERVABILITY_IP:16686
  Grafana:          http://OBSERVABILITY_IP:3001
  ClickHouse:       http://OBSERVABILITY_IP:8123
```

---

## 🛠️ **Troubleshooting**

### Services Not Starting?

```bash
# SSH to the server
ssh -i <SSH_KEY> root@<SERVER_IP>

# Check service status
supervisorctl status

# View logs
tail -f /var/log/mbta-*.log

# Restart services
supervisorctl restart all
```

### SLIM Agents Not Running?

```bash
# SSH to agents server
ssh -i mbta-agents-key root@AGENTS_IP

# Check SLIM service status
supervisorctl status mbta-alerts-slim
supervisorctl status mbta-planner-slim
supervisorctl status mbta-stopfinder-slim

# View SLIM logs
tail -50 /var/log/mbta-alerts-slim.err.log
tail -50 /var/log/mbta-planner-slim.err.log
tail -50 /var/log/mbta-stopfinder-slim.err.log

# If not running, restart
supervisorctl restart mbta-alerts-slim mbta-planner-slim mbta-stopfinder-slim
```

### SLIM Client Connection Issues?

```bash
# SSH to exchange server
ssh -i mbta-exchange-key root@EXCHANGE_IP

# Check if USE_SLIM is enabled
cat /opt/mbta-agentcy/.env | grep USE_SLIM
# Should show: USE_SLIM=true

# Check exchange logs for SLIM errors
tail -100 /var/log/mbta-exchange.err.log | grep -i slim

# Verify can reach SLIM agents
curl -v grpc://AGENTS_IP:50051/
# (gRPC connections won't respond like HTTP, but firewall should allow)
```

### MCP Client Issues?

```bash
# Check if mbta-mcp is installed
ssh -i mbta-exchange-key root@EXCHANGE_IP
source /opt/mbta-agentcy/venv/bin/activate
python -c "import mbta_mcp; print('OK')"

# Reinstall if needed
pip install git+https://github.com/cubismod/mbta-mcp.git --force-reinstall
supervisorctl restart mbta-exchange
```

### Agents Not Registered?

```bash
# Check registry
curl http://REGISTRY_IP:6900/list

# Re-register
bash register_agents.sh

# Verify each agent
curl http://REGISTRY_IP:6900/agents/mbta-alerts
curl http://REGISTRY_IP:6900/agents/mbta-planner
curl http://REGISTRY_IP:6900/agents/mbta-stopfinder
```

### No Traces in Jaeger?

```bash
# Check OTEL endpoint is configured
ssh -i mbta-exchange-key root@EXCHANGE_IP
cat /opt/mbta-agentcy/.env | grep OTEL

# Should show:
# OTEL_ENDPOINT=http://OBSERVABILITY_IP:4317

# If missing, add it:
echo "OTEL_ENDPOINT=http://OBSERVABILITY_IP:4317" >> /opt/mbta-agentcy/.env
supervisorctl restart all
```

---

## 🧹 **Cleanup / Deletion**

### Delete a Single Server

```bash
# Get instance ID from deployment output
linode-cli linodes delete <INSTANCE_ID>
```

### Delete Everything

```bash
# List all MBTA instances
linode-cli linodes list --format "id,label,tags" --text

# Delete each one
linode-cli linodes delete <ID1>
linode-cli linodes delete <ID2>
linode-cli linodes delete <ID3>
linode-cli linodes delete <ID4>
```

**Warning:** This permanently deletes all data and servers!

---



### Key Files for SLIM Implementation

**Exchange Agent (Client):**
- `/opt/mbta-agentcy/src/exchange_agent/slim_client.py` - Creates SLIM clients
- `/opt/mbta-agentcy/src/exchange_agent/stategraph_orchestrator.py` - Uses SLIM for A2A calls

**Agent Servers (Servers):**
- `/opt/mbta-agents/agents/alerts/slim_wrapper.py` - SLIM-enabled alerts agent
- `/opt/mbta-agents/agents/planner/slim_wrapper.py` - SLIM-enabled planner agent
- `/opt/mbta-agents/agents/stopfinder/slim_wrapper.py` - SLIM-enabled stopfinder agent

---

## 🔧 **Local Development (Optional)**

### Run Locally Without Deployment

**Requirements:**
- Python 3.12+
- Docker Desktop (for observability)

**Setup:**

```bash
# 1. Create virtual environment
python3.12 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Create .env file
cp .env.example .env
# Edit .env and add your API keys

# 4. Install SLIM SDKs
pip install agntcy-app-sdk a2a-sdk

# 5. Set SLIM mode
echo "USE_SLIM=true" >> .env

# 6. Start observability (optional)
docker compose -f docker-compose-observability.yml up -d

# 7. Run agents with SLIM (in separate terminals)
python -m agents.alerts.slim_wrapper
python -m agents.planner.slim_wrapper
python -m agents.stopfinder.slim_wrapper

# 8. Run exchange agent (uses SLIM client)
python -m src.exchange_agent.exchange_server

# 9. Run frontend
python -m src.frontend.chat_server
```

**Access locally:**
- Frontend: http://localhost:3000
- Exchange: http://localhost:8100
- Jaeger: http://localhost:16686

---

## 📚 **Project Structure**

```
mbta-agentcy/
├── src/
│   ├── exchange_agent/          # Protocol gateway & intelligent routing
│   │   ├── exchange_server.py   # Main FastAPI server
│   │   ├── mcp_client.py        # MCP protocol implementation
│   │   ├── slim_client.py       # SLIM transport client (A2A over gRPC)
│   │   └── stategraph_orchestrator.py  # A2A coordination via SLIM
│   │
│   ├── agents/                  # A2A specialized agents
│   │   ├── alerts/
│   │   │   ├── main.py          # HTTP A2A server (port 8001)
│   │   │   └── slim_wrapper.py  # SLIM A2A server (port 50051)
│   │   ├── planner/
│   │   │   ├── main.py          # HTTP A2A server (port 8002)
│   │   │   └── slim_wrapper.py  # SLIM A2A server (port 50052)
│   │   └── stopfinder/
│   │       ├── main.py          # HTTP A2A server (port 8003)
│   │       └── slim_wrapper.py  # SLIM A2A server (port 50053)
│   │
│   ├── frontend/                # Web UI
│   │   ├── chat_server.py       # WebSocket server
│   │   └── static/              # CSS/JS assets
│   │
│   └── observability/           # Telemetry & monitoring
│       ├── otel_config.py       # OpenTelemetry setup
│       ├── clickhouse_logger.py # Analytics logging
│       ├── metrics.py           # Metrics collection
│       └── traces.py            # Tracing utilities
│
├── docker/
│   └── otel-collector-config.yaml  # Telemetry routing config
│
├── deploy-agents-only.sh        # Deploy agents with SLIM
├── deploy-exchange-only.sh      # Deploy exchange with SLIM client
├── deploy-observability.sh      # Deploy observability
├── deploy_registry_with_ui.sh   # Deploy NANDA registry
├── register_agents.sh           # Register agents in registry
├── registry-ui.html             # Registry dashboard
├── docker-compose-observability.yml  # Optional local observability
└── requirements.txt             # Python dependencies
```

---

## 🧪 **Technology Stack**

### Protocols & Frameworks
- **MCP** - Anthropic's Model Context Protocol (stdio transport)
- **A2A** - NANDA/Google Agent-to-Agent protocol (HTTP/JSON)
- **SLIM** - Cisco transport for A2A over gRPC (binary, efficient)
- **HTTPS/TLS** - Secure transport between services

### Core Technologies
- **Python 3.12** - Primary language
- **FastAPI** - Web framework for all services
- **LangGraph** - Multi-agent orchestration
- **OpenAI GPT-4o-mini** - Classification, routing, synthesis
- **agntcy-app-sdk** - SLIM client factory
- **a2a-sdk** - A2A server and types

### Observability
- **OpenTelemetry** - Distributed tracing standard (OTLP over gRPC)
- **Jaeger** - Trace visualization
- **Grafana** - Metrics dashboards
- **ClickHouse** - Time-series analytics

### Infrastructure
- **Linode Cloud** - 4 Ubuntu 22.04 instances
- **Docker** - Observability containerization
- **Supervisor** - Process management for agents/exchange
- **Nginx** - Web server for registry UI

---

## 🔐 **Security Considerations**

### Current Implementation
✅ **HTTPS with TLS** between all services
✅ **API key authentication** for OpenAI/MBTA
✅ **Cloud firewall** rules restricting ports
✅ **SSH key-only** access (no password login)
✅ **SLIM transport** isolated on internal ports (50051-50053)

### Production Hardening Needed
⏭️ Mutual TLS (mTLS) for SLIM gRPC connections
⏭️ W3C Verifiable Credentials (NANDA spec)
⏭️ Ed25519 cryptographic signing for agents
⏭️ Zero Trust Agentic Access (ZTAA) framework
⏭️ Secrets management (Vault/AWS Secrets Manager)

**Note:** Current security is sufficient for research/demo. Production deployment serving real transit authority would require full security stack per NANDA specifications.

---

## 🔗 **Quick Links**

- **NANDA Project:** https://nanda.media.mit.edu/
- **AGNTCY Docs:** https://docs.agntcy.org/
- **MCP Specification:** https://modelcontextprotocol.io/
- **A2A Protocol:** https://github.com/google/a2a
- **SLIM Transport:** https://github.com/cisco-open/agentcy

---

## ⚡ **Quick Reference Commands**

```bash
# Deploy everything (run in order)
bash deploy_registry_with_ui.sh
bash deploy-agents-only.sh <MBTA_KEY> <OPENAI_KEY>
bash deploy-exchange-only.sh <OPENAI_KEY> <MBTA_KEY> <AGENTS_IP>
bash deploy-observability.sh
bash register_agents.sh

# Test the system
curl http://EXCHANGE_IP:8100/
curl -X POST http://EXCHANGE_IP:8100/chat -d '{"query":"Red Line delays?"}'

# Test SLIM agents directly
curl http://AGENTS_IP:50051/.well-known/agent-card.json
curl http://AGENTS_IP:50052/.well-known/agent-card.json
curl http://AGENTS_IP:50053/.well-known/agent-card.json

# Check SLIM services running
ssh -i mbta-agents-key root@AGENTS_IP
sudo supervisorctl status | grep slim

# View traces (including SLIM spans)
# Open: http://OBSERVABILITY_IP:16686

# Access UI
# Open: http://EXCHANGE_IP:3000

# SSH to servers
ssh -i mbta-exchange-key root@EXCHANGE_IP
ssh -i mbta-agents-key root@AGENTS_IP
ssh -i mbta-observability-key root@OBSERVABILITY_IP
ssh -i Northeastern-registry-v3-key root@REGISTRY_IP

# Delete everything
linode-cli linodes list
linode-cli linodes delete <INSTANCE_ID>
```

---

## 📖 **Documentation Files**

For deeper understanding of the system:
- **SLIM_and_AGNTCY_SDK_Explained.md** - Detailed explanation of SLIM transport and SDK usage
- **Architecture Diagrams** - Visual representation of protocol flows
- **Performance Analysis** - Benchmarks and latency breakdowns
- **Protocol Comparison** - MCP vs A2A vs SLIM characteristics
