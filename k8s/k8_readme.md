# MBTA Winter 2026

A distributed multi-agent transit intelligence system for Boston's MBTA network, with hybrid MCP+A2A protocol orchestration via SLIM transport, deployed on Akamai Cloud using Linode Kubernetes Engine (LKE).

## Overview

This system demonstrates **hybrid protocol orchestration** by combining multiple agent communication standards into a unified transit assistant:

- **MCP** (Model Context Protocol) — fast single-tool queries (~400ms)
- **A2A** (Agent-to-Agent) — complex multi-agent coordination (~1500ms)
- **SLIM** (Semantic Language Interface for Multi-agent) — efficient A2A transport over gRPC
- **NANDA Registry** — dynamic agent discovery and semantic lookup
- **Intelligent LLM routing** — 25x performance improvement for simple queries

The project supports two deployment modes:
1. **Cloud (LKE)** — Terraform-provisioned Kubernetes on Akamai Cloud
2. **Local development** — Docker Compose on your machine

## Technology Stack

- **Backend:** Python 3.11, FastAPI
- **Orchestration:** LangGraph, LangChain
- **AI/ML:** Anthropic Claude (primary) with OpenAI fallback
- **Protocols:** MCP, A2A, SLIM (Cisco agntcy-app-sdk, a2a-sdk)
- **Observability:** OpenTelemetry, Jaeger, Grafana, ClickHouse
- **Deployment:** Terraform → Linode Kubernetes Engine (LKE)
- **Local dev:** Docker Compose

## Architecture

```
┌─────────────┐     ┌────────────────┐     ┌────────────────┐
│  Frontend   │────▶│ Exchange Agent │────▶│ NANDA Registry │
│  (3000)     │ WS  │ (8100)         │     │ (6900)         │
└─────────────┘     └──┬──────────┬──┘     └────────────────┘
                       │          │
              ┌────────┘          └─────────┐
              ▼                             ▼
       ┌────────────┐            ┌───────────────────┐
       │ MCP Client │            │ SLIM A2A Transport│
       │ (stdio)    │            │                   │
       │ 32 tools   │            │ Alerts    (50051) │
       │ ~400ms     │            │ Planner   (50052) │
       └────────────┘            │ StopFinder(50053) │
                                 └───────────────────┘

┌─────────────────────────────────────────────────────┐
│  Observability: Jaeger (16686) · Grafana (3001)     │
│  ClickHouse (8123) · OTEL Collector (4317)          │
└─────────────────────────────────────────────────────┘
```

## Deployment

![MBTA System Deployment Diagram](deployment-1.png)

## Prerequisites

- [Linode/Akamai account](https://cloud.linode.com/) with API token
- [Anthropic API key](https://console.anthropic.com/) (primary LLM)
- [OpenAI API key](https://platform.openai.com/)
- [MBTA API key](https://api-v3.mbta.com/)
- [Terraform](https://developer.hashicorp.com/terraform/install) (≥ 1.0)
- [kubectl](https://kubernetes.io/docs/tasks/tools/)
- [Docker](https://docs.docker.com/get-docker/)

---
## Cloud Deployment (LKE)

### 1. Clone this repository

```bash
git clone https://github.com/DataWorksAI-com/MbtaWinter2026
cd MbtaWinter2026
```

### 2. Create a Linode API token

In the [Akamai Cloud Console](https://cloud.linode.com/profile/tokens), create an API token with **read/write** permissions for **Kubernetes** and **Linodes**, and **read** permissions for **Events**.

### 3. Configure Terraform variables

```bash
cp terraform/terraform.tfvars.example terraform/terraform.tfvars
# Edit terraform.tfvars — add your Linode API token
```

Key variables in `terraform/terraform.tfvars`:

| Variable | Default | Description |
|----------|---------|-------------|
| `linode_token` | *(required)* | Linode API token |
| `region` | `us-east` | Akamai Cloud region (Newark, NJ — close to Boston) |
| `cluster_label` | `mbta-winter-2026` | LKE cluster name |
| `k8s_version` | `1.34` | Kubernetes version |
| `lke_node_type` | `g6-standard-2` | Node size (4 GB shared) |
| `lke_node_count` | `3` | Worker node count |

### 4. Apply Terraform configuration

Provision the LKE cluster:

macOS:
```bash
cd terraform
terraform init
terraform plan
terraform apply
```
Windows:
```PowerShell
cd terraform
terraform init
terraform plan -var-file="terraform.tfvars"
terraform apply -var-file="terraform.tfvars"
```

### 5. Capture Terraform outputs

```bash
terraform output -json > terraform.output.json
```

The kubeconfig is automatically written to `terraform/kubeconfig.yaml`.

### 6. Configure kubectl

macOS:
```bash
export KUBECONFIG=$(pwd)/terraform/kubeconfig.yaml
kubectl get nodes
```
Windows:
```PowerShell
$env:KUBECONFIG="$PWD\kubeconfig.yaml"
kubectl get nodes
```
You should see your LKE worker nodes in `Ready` state.

> **Tip:** Add the export to your `~/.zshrc` or `~/.bashrc` so it persists across terminal sessions.

### 7. Create Kubernetes secrets

```bash
cd ..
cp k8s/secrets.example.yaml k8s/secrets.yaml
```

Edit `k8s/secrets.yaml` and replace placeholders with your **base64-encoded** API keys:

macOS:
```bash
echo -n "your-anthropic-api-key" | base64
echo -n "your-mbta-api-key" | base64
```
Windows:
```PowerShell
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("your-openai-api-key"))
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("your-mbta-api-key"))
```

> **Important:** Always use `echo -n` (no trailing newline) or the key will be invalid.

### 8. Build and push container images

Set your container registry (Docker Hub, Harbor, or any OCI registry):

macOS:
```bash
export DOCKER_REGISTRY=docker.io/youruser   # or your Harbor URL
bash deploy.sh build
bash deploy.sh push
```
Windows:
```PowerShell
$env:DOCKER_REGISTRY="docker.io/youruser"

docker build -f docker\Dockerfile.exchange -t $env:DOCKER_REGISTRY/mbta-exchange:1.0 .
docker build -f docker\Dockerfile.agent -t $env:DOCKER_REGISTRY/mbta-agent:1.0 .
docker build -f docker\Dockerfile.registry -t $env:DOCKER_REGISTRY/mbta-registry:1.0 .

docker push $env:DOCKER_REGISTRY/mbta-exchange:1.0
docker push $env:DOCKER_REGISTRY/mbta-agent:1.0
docker push $env:DOCKER_REGISTRY/mbta-registry:1.0
```

This builds three images:
- `mbta-exchange` — Exchange agent + frontend
- `mbta-agent` — Shared agent image (alerts, planner, stopfinder)
- `mbta-registry` — NANDA agent registry

### 9. Deploy to Kubernetes

macOS:
```bash
bash deploy.sh apply
```
Windows:
```PowerShell
kubectl apply -f k8s\
```

This will:
1. Create the `mbta` namespace
2. Apply ConfigMap and Secrets
3. Deploy all services (exchange, frontend, 3 agents, registry, observability)
4. Register agents in the NANDA registry
5. Expose the frontend via a LoadBalancer


### 10. Set up NGINX Ingress Controller

Install the ingress controller:
```bash
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm repo update
helm install ingress-nginx ingress-nginx/ingress-nginx \
  --namespace mbta \
  --set controller.service.type=LoadBalancer
```

Wait for the external IP:
```bash
kubectl --namespace mbta get services ingress-nginx-controller -w
```

> **Note:** Linode has a NodeBalancer limit per account. If the IP stays `<pending>`, you may need to delete existing NodeBalancers from the Linode dashboard or contact support to increase your limit.

Once you have the IP, update your DNS A records to point your subdomains to it, then apply the ingress routing rules:
```bash
kubectl apply -f k8s/ingress.yaml
```

**4. Update Step 11 Verify deployment** — replace the LoadBalancer IP instruction with the domain name:
```markdown
Open `http://mbta.yourdomain.com` in your browser.
```

**5. Add to Known Issues** — the httpx/anyio incompatibility that you discovered:
```markdown
- **httpx/anyio incompatibility in Kubernetes:** uvicorn uses uvloop which conflicts with httpx AsyncClient in certain async contexts. All internal HTTP calls use `urllib.request` wrapped in `asyncio.to_thread` instead of httpx to avoid this.
```

**6. Secrets section** — update to use `kubectl create secret` directly instead of the yaml file approach, since that's what actually worked:
```bash
kubectl create secret generic mbta-api-secrets -n mbta \
  --from-literal=OPENAI_API_KEY="sk-..." \
  --from-literal=MBTA_API_KEY="your-key"
```

Want me to generate the full updated README file with all these changes applied?


### 11. Fix the startup race condition

After deploy completes, restart the exchange so it picks up the now-ready registry:

```bash
kubectl -n mbta rollout restart deployment/exchange
```

Wait ~15 seconds, then confirm the A2A path is available:

```bash
kubectl -n mbta logs deploy/exchange --tail=20
# Should show: ✅ Registry validation passed - A2A path ready
```

If it still fails, re-register agents and restart again:

```bash
kubectl -n mbta delete job register-agents
kubectl apply -f k8s/register-agents-job.yaml
kubectl -n mbta logs job/register-agents -f
kubectl -n mbta rollout restart deployment/exchange
```


### 12. Verify deployment

```bash
# Check all pods are running
kubectl -n mbta get pods

# Get the frontend public IP
kubectl -n mbta get svc frontend \
  -o jsonpath='{.status.loadBalancer.ingress[0].ip}'
```

### 13. Test the system

Open `http://<FRONTEND_IP>:3000` in your browser and type a query in the chat interface.

To test via curl (port-forward exchange first):

macOS:
```bash
kubectl -n mbta port-forward svc/exchange 8100:8100 &
sleep 5

# Simple query (MCP fast path ~400ms)
curl -X POST http://localhost:8100/chat \
  -H "Content-Type: application/json" \
  -d '{"query": "Red Line delays?"}'
```
Windows:
```PowerShell
curl -Method POST http://$FRONTEND_IP`:8100/chat `
  -Headers @{"Content-Type"="application/json"} `
  -Body '{"query": "Red Line delays?"}'
```

#### Health check

macOS:
```bash
curl http://${FRONTEND_IP}:3000/
```
Windows:
```PowerShell
curl http://$FRONTEND_IP`:3000/
```

#### Simple query (MCP fast path ~400ms)

macOS:
```bash
curl -X POST http://exchange:8100/chat \
  -H "Content-Type: application/json" \
  -d '{"query": "Red Line delays?"}'
```
Windows:
```PowerShell
curl -Method POST http://$FRONTEND_IP`:8100/chat `
  -Headers @{"Content-Type"="application/json"} `
  -Body '{"query": "Red Line delays?"}'
```

#### Complex query (A2A via SLIM ~1500ms)

macOS:
```bash
curl -X POST http://localhost:8100/chat \
  -H "Content-Type: application/json" \
  -d '{"query": "How do I get from Northeastern to MIT?"}'
```
Windows:
```PowerShell
curl -Method POST http://$FRONTEND_IP`:8100/chat `
  -Headers @{"Content-Type"="application/json"} `
  -Body '{"query": "How do I get from Harvard to MIT?"}'
```

> **Note:** Start the port-forward before running curl. If you run both in the same command, curl may fire before the tunnel is established.

### 14. View distributed traces

Port-forward Jaeger to your machine:

```bash
kubectl -n mbta port-forward svc/jaeger 16686:16686
```

Open http://localhost:16686, select service `exchange-agent`, and click **Find Traces**.

---

## Local Development (Docker Compose)

For local development without cloud infrastructure:

### 1. Configure environment

```bash
cp .env.example .env
# Edit .env — add your ANTHROPIC_API_KEY (or OPENAI_API_KEY) and MBTA_API_KEY
```

### 2. Start all services

```bash
docker compose up --build
```

### 3. Access the system

| Service | URL |
|---------|-----|
| Frontend (Chat UI) | http://localhost:3000 |
| Exchange API | http://localhost:8100 |
| Jaeger (Traces) | http://localhost:16686 |
| Grafana (Metrics) | http://localhost:3001 |
| NANDA Registry | http://localhost:6900 |

### 4. Register agents (first time only)

macOS:
```bash
# Wait for services to start, then register agents
curl -s -X POST http://localhost:6900/register \
  -H "Content-Type: application/json" \
  -d '{"agent_id":"mbta-alerts","name":"MBTA Alerts Agent","agent_url":"http://alerts-agent:8001","status":"alive"}'

curl -s -X POST http://localhost:6900/register \
  -H "Content-Type: application/json" \
  -d '{"agent_id":"mbta-planner","name":"MBTA Planner Agent","agent_url":"http://planner-agent:8002","status":"alive"}'

curl -s -X POST http://localhost:6900/register \
  -H "Content-Type: application/json" \
  -d '{"agent_id":"mbta-stopfinder","name":"MBTA StopFinder Agent","agent_url":"http://stopfinder-agent:8003","status":"alive"}'

```
Windows:
```PowerShell
$REGISTRY_URL = "http://localhost:6900/register"

curl.exe -s -X POST $REGISTRY_URL `
  -H "Content-Type: application/json" `
  -d '{"agent_id":"mbta-alerts","name":"MBTA Alerts Agent","agent_url":"http://alerts-agent:8001","status":"alive"}'

curl.exe -s -X POST $REGISTRY_URL `
  -H "Content-Type: application/json" `
  -d '{"agent_id":"mbta-planner","name":"MBTA Planner Agent","agent_url":"http://planner-agent:8002","status":"alive"}'

curl.exe -s -X POST $REGISTRY_URL `
  -H "Content-Type: application/json" `
  -d '{"agent_id":"mbta-stopfinder","name":"MBTA StopFinder Agent","agent_url":"http://stopfinder-agent:8003","status":"alive"}'
```

### 5. Add agent status

macOS:
```bash
curl -s -X PUT http://registry:6900/agents/mbta-alerts/status \
  -H "Content-Type: application/json" \
  -d '{"alive": true, "capabilities": ["alerts", "service_status", "delays", "disruptions"], "description": "Monitors and reports MBTA service alerts, delays, and disruptions for all transit lines including Red, Orange, Blue, Green, Silver, Commuter Rail, Bus, and Ferry. Provides real-time alert information."}'

curl -s -X PUT http://registry:6900/agents/mbta-planner/status \
  -H "Content-Type: application/json" \
  -d '{"alive": true, "capabilities": ["trip_planning", "route_finding", "transfers", "schedules"], "description": "Plans transit routes between locations in the Boston MBTA network. Can find optimal routes, transfers, and provide trip planning assistance using real-time schedule data."}'

curl -s -X PUT http://registry:6900/agents/mbta-stopfinder/status \
  -H "Content-Type: application/json" \
  -d '{"alive": true, "capabilities": ["stop_search", "station_info", "accessibility", "nearby_stops"], "description": "Finds MBTA stops and stations by name, location, or route. Provides stop details including accessibility, available routes, and nearby connections."}'
```
Windows:
```PowerShell
curl.exe -s -X PUT $REGISTRY_URL `
  -H "Content-Type: application/json" `
  -d '{"alive": true, "capabilities": ["alerts", "service_status", "delays", "disruptions"], "description": "Monitors and reports MBTA service alerts, delays, and disruptions for all transit lines including Red, Orange, Blue, Green, Silver, Commuter Rail, Bus, and Ferry. Provides real-time alert information."}'

curl.exe -s -X PUT $REGISTRY_URL `
  -H "Content-Type: application/json" `
  -d '{"alive": true, "capabilities": ["trip_planning", "route_finding", "transfers", "schedules"], "description": "Plans transit routes between locations in the Boston MBTA network. Can find optimal routes, transfers, and provide trip planning assistance using real-time schedule data."}'

curl.exe -s -X PUT $REGISTRY_URL `
  -H "Content-Type: application/json" `
  -d '{"alive": true, "capabilities": ["stop_search", "station_info", "accessibility", "nearby_stops"], "description": "Finds MBTA stops and stations by name, location, or route. Provides stop details including accessibility, available routes, and nearby connections."}'
```

### 6. Stop services

```bash
docker compose down          # Stop containers
docker compose down -v       # Stop and remove volumes
```

---

## Project Structure

```
MbtaWinter2026/
├── src/
│   ├── exchange_agent/
│   │   ├── exchange_server.py          # FastAPI server (port 8100)
│   │   ├── llm_client.py               # NEW: provider-agnostic LLM wrapper (Anthropic/OpenAI)
│   │   ├── mcp_client.py               # MCP stdio client
│   │   ├── slim_client.py              # SLIM transport client
│   │   └── stategraph_orchestrator.py  # LangGraph A2A orchestration
│   ├── agents/
│   │   ├── alerts/                     # Service alerts (8001 / 50051)
│   │   ├── planner/                    # Trip planning (8002 / 50052) — transfer routing added
│   │   └── stopfinder/                 # Stop search (8003 / 50053)
│   ├── frontend/                       # Chat UI (port 3000)
│   ├── registry/                       # NANDA agent registry (port 6900)
│   └── observability/                  # OTel, metrics, traces
│
├── terraform/                          # LKE infrastructure
├── k8s/                                # Kubernetes manifests
│   ├── configmap.yaml                  # Now includes LLM_PROVIDER, ANTHROPIC_MODEL, OPENAI_MODEL
│   ├── secrets.yaml                    # Now includes ANTHROPIC_API_KEY
│   ├── register-agents-job.yaml        # Now marks agents alive after registering
│   └── ...
├── docker-compose.yaml
├── deploy.sh
├── requirements.txt                    # Now includes anthropic>=0.84.0
└── .env.example
```

---

## Configuration Reference

| Variable | Service | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | Exchange, Planner | Anthropic API key (primary LLM) |
| `OPENAI_API_KEY` | Exchange, Planner | OpenAI API key (optional fallback) |
| `LLM_PROVIDER` | Exchange, Planner | Force `anthropic` or `openai`; auto-detected if unset |
| `ANTHROPIC_MODEL` | Exchange, Planner | Claude model. Default: `claude-haiku-4-5-20251001` |
| `OPENAI_MODEL` | Exchange, Planner | OpenAI model. Default: `gpt-4o-mini` |
| `MBTA_API_KEY` | All agents | MBTA v3 API key |
| `USE_SLIM` | Exchange | Enable SLIM transport (`true`/`false`) |
| `REGISTRY_URL` | Exchange | NANDA registry endpoint |
| `ENABLE_FEDERATION` | Registry | Enable switchboard-style federated lookups across registries |
| `AGNTCY_ADS_URL` | Registry | AGNTCY ADS base URL used by the federation adapter |
| `AGNTCY_ADS_SEARCH_PATH` | Registry | AGNTCY search path (default: `/v1/search`) |
| `AGNTCY_ADS_TOKEN` | Registry (secret) | Optional bearer token for AGNTCY ADS |
| `NEU_REGISTRY_URL` | Registry | Northeastern registry base URL used by the federation adapter |
| `ENABLE_EXTERNAL_REGISTRATION` | Register job | Mirror MBTA agent registrations to external registries |
| `NEU_REGISTRY_REGISTER_URL` | Register job | External NEU register endpoint for mirroring |
| `AGNTCY_REGISTER_WEBHOOK_URL` | Register job | External AGNTCY registration webhook for mirroring |
| `EXCHANGE_AGENT_URL` | Frontend | Exchange server endpoint |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | All | OpenTelemetry collector |
| `CLICKHOUSE_HOST` | Exchange | ClickHouse analytics host |

---

## API Endpoints

### Exchange Agent (port 8100)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Health check |
| `POST` | `/chat` | Send a query (auto-routes MCP vs A2A) |

### Agents (ports 8001–8003)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check |
| `POST` | `/a2a/message` | A2A message endpoint |
| `GET` | `/alerts?route=Red` | Direct alerts query (alerts agent) |
| `GET` | `/plan?origin=X&destination=Y` | Direct plan query (planner agent) |
| `GET` | `/stops?query=X` | Direct stop query (stopfinder agent) |

### Registry (port 6900)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check |
| `GET` | `/list` | List registered agents |
| `POST` | `/register` | Register an agent |
| `GET` | `/switchboard/registries` | Show local and federated registry connectivity |
| `GET` | `/switchboard/lookup/@neu:<agent>` | Federated lookup through Northeastern adapter |
| `GET` | `/switchboard/lookup/@agntcy:<agent>` | Federated lookup through AGNTCY adapter |

## Federation Setup And Verification

1. Enable federation in [k8s/configmap.yaml](k8s/configmap.yaml) by setting:
  - `ENABLE_FEDERATION: "true"`
  - `AGNTCY_ADS_URL` to your AGNTCY ADS endpoint
  - `NEU_REGISTRY_URL` to your Northeastern registry endpoint
2. If AGNTCY requires auth, add `AGNTCY_ADS_TOKEN` in [k8s/secrets.yaml](k8s/secrets.yaml).
3. Apply updated manifests:

```bash
bash deploy.sh apply
```

4. Verify connected registries:

```bash
kubectl -n mbta port-forward svc/registry 6900:6900
curl http://localhost:6900/switchboard/registries
```

5. Verify cross-registry lookup via switchboard-style IDs:

```bash
curl "http://localhost:6900/switchboard/lookup/@neu:mbta-alerts"
curl "http://localhost:6900/switchboard/lookup/@agntcy:mbta-alerts"
```

6. Optional: enable mirrored registration by setting in [k8s/configmap.yaml](k8s/configmap.yaml):
  - `ENABLE_EXTERNAL_REGISTRATION: "true"`
  - `NEU_REGISTRY_REGISTER_URL`
  - `AGNTCY_REGISTER_WEBHOOK_URL`

When mirroring is enabled, [k8s/register-agents-job.yaml](k8s/register-agents-job.yaml) will register local MBTA agents and then forward registration payloads to configured external targets.

---

## Known Issues & Limitations

- **Transfer routing latency:** `find_transfer_routes()` makes additional MBTA API calls, so responses for transfer routes take 5–10 seconds longer than direct routes.
- **One transfer only:** Journeys requiring two or more transfers will fall back to a "check mbta.com" message.
- **ClickHouse logging disabled by default:** The `mbta_logs` database must be created manually after first deploy.
- **Startup race condition:** Exchange validates registry at boot. If the registry isn't ready yet, the A2A path gets disabled. Fix: `kubectl -n mbta rollout restart deployment/exchange` after all pods are running.
- **NANDA registry `alive=false` default:** The registry sets `alive=false` on new registrations. The `register-agents-job` works around this with a separate `PUT /status` call after each registration.

---

## Cleanup

### Delete Kubernetes resources

macOS:
```bash
bash deploy.sh destroy
```
Windows:
```PowerShell
kubectl delete -f k8s\
```

### Destroy LKE cluster (Terraform)

macOS:
```bash
cd terraform
terraform destroy
```
Windows:
```PowerShell
cd terraform
terraform destroy -var-file="terraform.tfvars"
```

### Remove local Docker resources

```bash
docker compose down -v
```
macOS:
```bash
docker rmi $(docker images 'mbta-*' -q) 2>/dev/null || true
```
Windows:
```PowerShell
docker images "mbta-*" -q | ForEach-Object { docker rmi $_ }
```

---

## Redeploy (no cleanup)

If the LKE cluster already exists and you did not run cleanup, you can redeploy in-place:

macOS:
```bash
# Build and push updated images
export DOCKER_REGISTRY=docker.io/youruser
bash deploy.sh build

# Re-apply manifests
bash deploy.sh apply
```
Windows:
```PowerShell
$env:DOCKER_REGISTRY="docker.io/youruser"

docker build -f docker\Dockerfile.exchange -t $env:DOCKER_REGISTRY/mbta-exchange:1.0 .
docker build -f docker\Dockerfile.agent -t $env:DOCKER_REGISTRY/mbta-agent:1.0 .
docker build -f docker\Dockerfile.registry -t $env:DOCKER_REGISTRY/mbta-registry:1.0 .

docker push $env:DOCKER_REGISTRY/mbta-exchange:1.0
docker push $env:DOCKER_REGISTRY/mbta-agent:1.0
docker push $env:DOCKER_REGISTRY/mbta-registry:1.0
```
If you only changed Kubernetes manifests and not the images:

macOS:
```bash
bash deploy.sh apply
```
Windows:
```PowerShell
kubectl apply -f k8s\
```

## Troubleshooting

### A2A path unavailable / StateGraph orchestrator not available

This happens when exchange starts before the registry is ready. Fix:

macOS:
```bash
kubectl -n mbta rollout restart deployment/exchange
kubectl -n mbta logs deploy/exchange --tail=20
# Look for: ✅ Registry validation passed - A2A path ready
```

If still failing, re-register agents first:

```bash
kubectl -n mbta delete job register-agents
kubectl apply -f k8s/register-agents-job.yaml
kubectl -n mbta logs job/register-agents -f
kubectl -n mbta rollout restart deployment/exchange
```

### Trip planning queries returning alerts instead of routes

Indicates the planner agent isn't registered or can't be reached. Check:

macOS:
```bash
kubectl -n mbta exec deploy/exchange -- curl -s http://registry:6900/list
kubectl -n mbta logs deploy/planner-agent -c planner-http --tail=50
```
Windows:
```PowerShell
kubectl apply -f k8s\
```

### `namespaces "mbta" not found`

Your `KUBECONFIG` isn't pointing at the LKE cluster:

```bash
export KUBECONFIG=$(pwd)/terraform/kubeconfig.yaml
kubectl get nodes  # should show LKE nodes
```

### Pods not starting?

```bash
kubectl -n mbta get pods
kubectl -n mbta describe pod <pod-name>
kubectl -n mbta logs <pod-name> -c <container-name>
```

### Quick logs for each service

```bash
kubectl -n mbta logs deploy/exchange --tail=200
kubectl -n mbta logs deploy/frontend --tail=200
kubectl -n mbta logs deploy/alerts-agent -c alerts-http --tail=200
kubectl -n mbta logs deploy/planner-agent -c planner-http --tail=200
kubectl -n mbta logs deploy/stopfinder-agent -c stopfinder-http --tail=200
kubectl -n mbta logs deploy/registry -c registry --tail=200
kubectl -n mbta logs deploy/otel-collector --tail=200
kubectl -n mbta logs deploy/jaeger --tail=200
kubectl -n mbta logs deploy/clickhouse --tail=200
kubectl -n mbta logs deploy/grafana --tail=200
```

### SLIM agents not responding?

```bash
kubectl -n mbta logs -l app=alerts-agent -c alerts-http
kubectl -n mbta logs -l app=alerts-agent -c alerts-slim
```

### No traces in Jaeger?

```bash
kubectl -n mbta logs deploy/otel-collector
```

---

## Observability

| Tool | Access |
|------|--------|
| **Jaeger (traces)** | `kubectl port-forward svc/jaeger 16686:16686 -n mbta` then open http://localhost:16686 |
| **Grafana (metrics)** | `kubectl port-forward svc/grafana 3001:3001 -n mbta` then open http://localhost:3001 (admin/admin) |
| **Exchange logs** | `kubectl -n mbta logs deploy/exchange --tail=50 -f` |
| **Planner logs** | `kubectl -n mbta logs deploy/planner-agent -c planner-http --tail=50 -f` |
| **All pods status** | `kubectl -n mbta get pods` |

---


## Links

- [NANDA Project](https://nanda.media.mit.edu/)
- [AGNTCY / SLIM Docs](https://docs.agntcy.org/)
- [MCP Specification](https://modelcontextprotocol.io/)
- [A2A Protocol](https://github.com/google/a2a)
- [Akamai LKE Docs](https://www.linode.com/docs/products/compute/kubernetes/)
- [Terraform Linode Provider](https://registry.terraform.io/providers/linode/linode/latest/docs)
- [Anthropic API Docs](https://docs.anthropic.com/)
