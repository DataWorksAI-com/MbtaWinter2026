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
- **AI/ML:** OpenAI GPT-4o-mini (routing, synthesis, extraction)
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
       │ MCP Client │            │ SLIM A2A Transport │
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

## Prerequisites

- [Linode/Akamai account](https://cloud.linode.com/) with API token
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

```bash
cd terraform
terraform init
terraform plan
terraform apply
```

### 5. Capture Terraform outputs

```bash
terraform output -json > terraform.output.json
```

The kubeconfig is automatically written to `terraform/kubeconfig.yaml`.

### 6. Configure kubectl

```bash
export KUBECONFIG=$(pwd)/kubeconfig.yaml
kubectl get nodes
```

You should see your LKE worker nodes in `Ready` state.

### 7. Create Kubernetes secrets

```bash
cd ..
cp k8s/secrets.example.yaml k8s/secrets.yaml
```

Edit `k8s/secrets.yaml` and replace the placeholder values with your base64-encoded API keys:

```bash
echo -n "your-openai-api-key" | base64
echo -n "your-mbta-api-key" | base64
```

### 8. Build and push container images

Set your container registry (Docker Hub, Harbor, or any OCI registry):

```bash
export DOCKER_REGISTRY=docker.io/youruser   # or your Harbor URL
bash deploy.sh build
bash deploy.sh push
```

This builds three images:
- `mbta-exchange` — Exchange agent + frontend
- `mbta-agent` — Shared agent image (alerts, planner, stopfinder)
- `mbta-registry` — NANDA agent registry

### 9. Deploy to Kubernetes

```bash
bash deploy.sh apply
```

This will:
1. Create the `mbta` namespace
2. Apply ConfigMap and Secrets
3. Deploy all services (exchange, frontend, 3 agents, registry, observability)
4. Register agents in the NANDA registry
5. Expose the frontend via a LoadBalancer

### 10. Verify deployment

```bash
# Check all pods are running
kubectl -n mbta get pods

# Get the frontend public IP
kubectl -n mbta get svc frontend \
  -o jsonpath='{.status.loadBalancer.ingress[0].ip}'
```

### 11. Test the system

```bash
FRONTEND_IP=$(kubectl -n mbta get svc frontend -o jsonpath='{.status.loadBalancer.ingress[0].ip}')

# Health check
curl http://${FRONTEND_IP}:3000/

# Simple query (MCP fast path ~400ms)
curl -X POST http://exchange:8100/chat \
  -H "Content-Type: application/json" \
  -d '{"query": "Red Line delays?"}'

# Complex query (A2A via SLIM ~1500ms)
curl -X POST http://exchange:8100/chat \
  -H "Content-Type: application/json" \
  -d '{"query": "How do I get from Harvard to MIT?"}'
```

Open `http://<FRONTEND_IP>:3000` in your browser to use the chat interface.

### 12. View distributed traces

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
# Edit .env — add your OPENAI_API_KEY and MBTA_API_KEY
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

### 5. Stop services

```bash
docker compose down          # Stop containers
docker compose down -v       # Stop and remove volumes
```

---

## Project Structure

```
MbtaWinter2026/
├── src/
│   ├── exchange_agent/              # Protocol gateway + routing
│   │   ├── exchange_server.py       # FastAPI server (port 8100)
│   │   ├── mcp_client.py            # MCP stdio client
│   │   ├── slim_client.py           # SLIM transport client
│   │   └── stategraph_orchestrator.py # LangGraph A2A orchestration
│   ├── agents/                      # A2A specialized agents
│   │   ├── alerts/                  # Service alerts (8001 / 50051)
│   │   ├── planner/                 # Trip planning (8002 / 50052)
│   │   └── stopfinder/              # Stop search (8003 / 50053)
│   ├── frontend/                    # Chat UI (port 3000)
│   │   ├── chat_server.py
│   │   └── static/
│   ├── registry/                    # NANDA agent registry (port 6900)
│   │   ├── registry.py              # Flask registry server
│   │   ├── agent_facts_server.py    # Agent facts API
│   │   ├── requirements.txt         # Registry-specific deps
│   │   └── static/
│   │       └── registry-ui.html     # Dashboard UI
│   └── observability/               # OTel, metrics, traces
│       ├── otel_config.py
│       ├── clickhouse_logger.py
│       ├── metrics.py
│       └── traces.py
│
├── terraform/                       # LKE infrastructure
│   ├── main.tf                      # LKE cluster resource
│   ├── variables.tf                 # Input variables
│   ├── outputs.tf                   # Cluster outputs
│   └── terraform.tfvars.example     # Variable template
│
├── k8s/                             # Kubernetes manifests
│   ├── namespace.yaml
│   ├── configmap.yaml
│   ├── secrets.example.yaml
│   ├── exchange.yaml
│   ├── frontend.yaml
│   ├── alerts-agent.yaml
│   ├── planner-agent.yaml
│   ├── stopfinder-agent.yaml
│   ├── registry.yaml
│   ├── observability.yaml
│   └── register-agents-job.yaml
│
├── docker/                          # Dockerfiles + configs
│   ├── Dockerfile.exchange
│   ├── Dockerfile.agent
│   ├── Dockerfile.registry
│   └── otel-collector-config.yaml
│
├── charts/
│   └── workload.example.yaml        # App Platform workload template
│
├── docker-compose.yaml              # Local development
├── deploy.sh                        # Build/push/deploy helper
├── requirements.txt                 # Python dependencies
├── .env.example                     # Environment variable template
└── .gitignore
```

## Configuration

The system uses environment variables for all configuration. Key variables:

| Variable | Service | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | Exchange, Planner | OpenAI API key |
| `MBTA_API_KEY` | All agents | MBTA v3 API key |
| `USE_SLIM` | Exchange | Enable SLIM transport (`true`/`false`) |
| `REGISTRY_URL` | Exchange | NANDA registry endpoint |
| `EXCHANGE_AGENT_URL` | Frontend | Exchange server endpoint |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | All | OpenTelemetry collector |
| `CLICKHOUSE_HOST` | Exchange | ClickHouse analytics host |

In Kubernetes, these are managed via ConfigMap (`k8s/configmap.yaml`) and Secrets (`k8s/secrets.yaml`).

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

## Cleanup

### Delete Kubernetes resources

```bash
bash deploy.sh destroy
```

### Destroy LKE cluster (Terraform)

```bash
cd terraform
terraform destroy
```

### Remove local Docker resources

```bash
docker compose down -v
docker rmi $(docker images 'mbta-*' -q) 2>/dev/null || true
```

## Redeploy (no cleanup)

If the LKE cluster already exists and you did not run cleanup, you can redeploy in-place:

```bash
# Build and push updated images
export DOCKER_REGISTRY=docker.io/youruser
bash deploy.sh build

# Re-apply manifests
bash deploy.sh apply
```

If you only changed Kubernetes manifests and not the images:

```bash
bash deploy.sh apply
```

## Troubleshooting

### Pods not starting?

```bash
kubectl -n mbta get pods
kubectl -n mbta describe pod <pod-name>
kubectl -n mbta logs <pod-name> -c <container-name>
```

### Quick logs for each service

```bash
# Exchange + frontend
kubectl -n mbta logs deploy/exchange --tail=200
kubectl -n mbta logs deploy/frontend --tail=200

# Agents (HTTP container)
kubectl -n mbta logs deploy/alerts-agent -c alerts-http --tail=200
kubectl -n mbta logs deploy/planner-agent -c planner-http --tail=200
kubectl -n mbta logs deploy/stopfinder-agent -c stopfinder-http --tail=200

# Registry
kubectl -n mbta logs deploy/registry -c registry --tail=200

# Observability
kubectl -n mbta logs deploy/otel-collector --tail=200
kubectl -n mbta logs deploy/jaeger --tail=200
kubectl -n mbta logs deploy/clickhouse --tail=200
kubectl -n mbta logs deploy/grafana --tail=200
```

### SLIM agents not responding?

```bash
# Check both containers in an agent pod
kubectl -n mbta logs -l app=alerts-agent -c alerts-http
kubectl -n mbta logs -l app=alerts-agent -c alerts-slim
```

### Agents not registered?

```bash
# Check registry
kubectl -n mbta exec deploy/exchange -- curl -s http://registry:6900/list

# Re-run registration job
kubectl -n mbta delete job register-agents
kubectl apply -f k8s/register-agents-job.yaml
```

### No traces in Jaeger?

```bash
# Verify OTEL collector is receiving data
kubectl -n mbta logs deploy/otel-collector
```

---

## Links

- [NANDA Project](https://nanda.media.mit.edu/)
- [AGNTCY / SLIM Docs](https://docs.agntcy.org/)
- [MCP Specification](https://modelcontextprotocol.io/)
- [A2A Protocol](https://github.com/google/a2a)
- [Akamai LKE Docs](https://www.linode.com/docs/products/compute/kubernetes/)
- [Terraform Linode Provider](https://registry.terraform.io/providers/linode/linode/latest/docs)
