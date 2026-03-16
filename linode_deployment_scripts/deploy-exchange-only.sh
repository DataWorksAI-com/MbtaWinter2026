#!/bin/bash

# ============================================================================
# MBTA Exchange + UI - Separate Deployment (No Agents)
# ============================================================================
# Deploys only Exchange Agent + Frontend UI to a separate Linode instance
# Agents must be deployed separately using deploy-agents-only.sh
# ============================================================================

set -e

OPENAI_API_KEY="$1"
MBTA_API_KEY="$2"
AGENTS_IP="$3"
REGION="${4:-us-east}"
INSTANCE_TYPE="${5:-g6-standard-4}"
ROOT_PASSWORD="${6:-}"

if [ -z "$OPENAI_API_KEY" ] || [ -z "$MBTA_API_KEY" ] || [ -z "$AGENTS_IP" ]; then
    echo "❌ Usage: $0 <OPENAI_KEY> <MBTA_KEY> <AGENTS_IP> [REGION] [INSTANCE_TYPE]"
    echo ""
    echo "Example:"
    echo "  bash deploy-exchange-only.sh \"sk-proj-...\" \"c845...\" \"172.104.25.25\""
    exit 1
fi

if [ -z "$ROOT_PASSWORD" ]; then
    ROOT_PASSWORD=$(openssl rand -base64 32 | tr -d "=+/" | cut -c1-25)
fi

FIREWALL_LABEL="mbta-exchange-firewall"
SSH_KEY_LABEL="mbta-exchange-key"
IMAGE_ID="linode/ubuntu22.04"
DEPLOYMENT_ID=$(date +%Y%m%d-%H%M%S)

echo "🚇 MBTA Exchange + UI - Separate Deployment"
echo "Deployment ID: $DEPLOYMENT_ID"
echo "Agents IP: $AGENTS_IP"
echo ""

# [1/9] Check Linode CLI
echo "[1/9] Checking Linode CLI..."
if ! linode-cli --version >/dev/null 2>&1; then
    echo "❌ Linode CLI not installed"
    exit 1
fi
echo "✅ Linode CLI ready"

# [2/9] Package exchange + frontend ONLY (exclude agents)
echo "[2/9] Packaging exchange + frontend..."

if [ ! -d "src/exchange_agent" ]; then
    echo "❌ src/exchange_agent/ not found! Run from mbta/ folder"
    exit 1
fi

TARBALL_NAME="mbta-exchange-${DEPLOYMENT_ID}.tar.gz"

# Package ONLY exchange, frontend, observability, docker
# EXPLICITLY EXCLUDE agents folder
tar -czf "/tmp/$TARBALL_NAME" \
    --exclude='agents' \
    --exclude='src/agents' \
    --exclude='venv' \
    --exclude='.venv' \
    --exclude='.git' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='*.pyo' \
    --exclude='.env' \
    --exclude='*.log' \
    --exclude='*.key' \
    --exclude='*.key.pub' \
    src/exchange_agent/ \
    src/frontend/ \
    src/observability/ \
    docker/ \
    docker-compose-observability.yml \
    2>/dev/null || true

TARBALL_SIZE=$(du -h "/tmp/$TARBALL_NAME" | cut -f1)
TARBALL_BYTES=$(du -b "/tmp/$TARBALL_NAME" | cut -f1)

echo "✅ Packaged: $TARBALL_SIZE"

if [ "$TARBALL_BYTES" -gt 10485760 ]; then  # 10MB
    echo "⚠️  Warning: Package is larger than expected ($TARBALL_SIZE)"
    echo "   Check contents: tar -tzf /tmp/$TARBALL_NAME | head -50"
fi

# [3/9] Setup firewall
echo "[3/9] Setting up firewall..."
FIREWALL_ID=$(linode-cli firewalls list --text --no-headers --format="id,label" | grep "$FIREWALL_LABEL" | cut -f1 || echo "")

INBOUND_RULES='[
    {"protocol": "TCP", "ports": "22", "addresses": {"ipv4": ["0.0.0.0/0"]}, "action": "ACCEPT"},
    {"protocol": "TCP", "ports": "3000", "addresses": {"ipv4": ["0.0.0.0/0"]}, "action": "ACCEPT"},
    {"protocol": "TCP", "ports": "8100", "addresses": {"ipv4": ["0.0.0.0/0"]}, "action": "ACCEPT"},
    {"protocol": "TCP", "ports": "16686", "addresses": {"ipv4": ["0.0.0.0/0"]}, "action": "ACCEPT"},
    {"protocol": "TCP", "ports": "3001", "addresses": {"ipv4": ["0.0.0.0/0"]}, "action": "ACCEPT"}
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
    echo "✅ Using existing firewall: $FIREWALL_ID"
fi

# [4/9] Setup SSH key
echo "[4/9] Setting up SSH key..."
if [ ! -f "${SSH_KEY_LABEL}.pub" ]; then
    ssh-keygen -t rsa -b 4096 -f "$SSH_KEY_LABEL" -N "" -C "mbta-exchange-$DEPLOYMENT_ID" >/dev/null 2>&1
    echo "✅ Generated SSH key"
else
    echo "✅ Using existing SSH key"
fi

# [5/9] Launch instance
echo "[5/9] Launching Linode..."
INSTANCE_ID=$(linode-cli linodes create \
    --type "$INSTANCE_TYPE" \
    --region "$REGION" \
    --image "$IMAGE_ID" \
    --label "mbta-exchange-$DEPLOYMENT_ID" \
    --tags "MBTA-Exchange" \
    --root_pass "$ROOT_PASSWORD" \
    --authorized_keys "$(cat ${SSH_KEY_LABEL}.pub)" \
    --firewall_id "$FIREWALL_ID" \
    --text --no-headers --format="id")

echo "✅ Instance ID: $INSTANCE_ID"

echo "   Waiting for instance..."
while true; do
    STATUS=$(linode-cli linodes view "$INSTANCE_ID" --text --no-headers --format="status")
    [ "$STATUS" = "running" ] && break
    sleep 5
done

PUBLIC_IP=$(linode-cli linodes view "$INSTANCE_ID" --text --no-headers --format="ipv4")
echo "✅ Public IP: $PUBLIC_IP"

# [6/9] Wait for SSH
echo "[6/9] Waiting for SSH..."
for i in {1..60}; do
    if ssh -i "$SSH_KEY_LABEL" -o StrictHostKeyChecking=no -o ConnectTimeout=5 \
        "root@$PUBLIC_IP" "echo ready" >/dev/null 2>&1; then
        echo "✅ SSH ready"
        break
    fi
    [ $i -eq 60 ] && { echo "❌ SSH timeout"; exit 1; }
    sleep 5
done

# [7/9] Upload code
echo "[7/9] Uploading exchange + frontend ($TARBALL_SIZE)..."

if scp -i "$SSH_KEY_LABEL" -o StrictHostKeyChecking=no -o ServerAliveInterval=30 \
    "/tmp/$TARBALL_NAME" "root@$PUBLIC_IP:/tmp/"; then
    echo "✅ Upload successful!"
else
    echo "❌ Upload failed"
    exit 1
fi

# [8/9] Install packages
echo "[8/9] Installing system packages (5-10 min)..."
ssh -i "$SSH_KEY_LABEL" -o StrictHostKeyChecking=no "root@$PUBLIC_IP" bash << 'PACKAGES'
set -e
export DEBIAN_FRONTEND=noninteractive

apt-get update -y >/dev/null 2>&1
apt-get install -y software-properties-common >/dev/null 2>&1

add-apt-repository -y ppa:deadsnakes/ppa >/dev/null 2>&1
apt-get update -y >/dev/null 2>&1
apt-get install -y python3.11 python3.11-venv python3.11-dev python3-pip git supervisor \
    docker.io docker-compose >/dev/null 2>&1

systemctl enable docker >/dev/null 2>&1
systemctl start docker >/dev/null 2>&1

echo "✅ Packages installed"
PACKAGES

# [9/9] Setup services
echo "[9/9] Configuring exchange + frontend (5-10 min)..."

ssh -i "$SSH_KEY_LABEL" -o StrictHostKeyChecking=no "root@$PUBLIC_IP" bash << SETUP
set -e

cd /opt
mkdir -p mbta-agentcy
cd mbta-agentcy

tar -xzf /tmp/$TARBALL_NAME
rm /tmp/$TARBALL_NAME

# Create Python 3.11 venv
python3.11 -m venv venv
source venv/bin/activate

pip install --upgrade pip >/dev/null 2>&1

# Install dependencies
pip install fastapi uvicorn httpx openai scikit-learn numpy pydantic \
    python-dotenv websockets langgraph langchain-core \
    opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp \
    >/dev/null 2>&1

# Install mbta-mcp
pip install git+https://github.com/cubismod/mbta-mcp.git >/dev/null 2>&1

echo "✅ Dependencies installed"

# Create .env
cat > .env << ENV
OPENAI_API_KEY=$OPENAI_API_KEY
MBTA_API_KEY=$MBTA_API_KEY
PYTHONPATH=/opt/mbta-agentcy
ENV

# Start observability if docker-compose exists
if [ -f docker-compose-observability.yml ]; then
    echo "Starting observability..."
    docker compose -f docker-compose-observability.yml up -d 2>/dev/null || echo "⚠️  Observability skipped"
    sleep 10
fi

# Supervisor config - Exchange Agent
cat > /etc/supervisor/conf.d/mbta-exchange.conf << 'S1'
[program:mbta-exchange]
command=/opt/mbta-agentcy/venv/bin/python -m src.exchange_agent.exchange_server
directory=/opt/mbta-agentcy
autostart=true
autorestart=true
stderr_logfile=/var/log/mbta-exchange.err.log
stdout_logfile=/var/log/mbta-exchange.out.log
environment=PYTHONPATH="/opt/mbta-agentcy"
S1

# Supervisor config - Frontend UI
cat > /etc/supervisor/conf.d/mbta-frontend.conf << 'S2'
[program:mbta-frontend]
command=/opt/mbta-agentcy/venv/bin/python -m src.frontend.chat_server
directory=/opt/mbta-agentcy
autostart=true
autorestart=true
stderr_logfile=/var/log/mbta-frontend.err.log
stdout_logfile=/var/log/mbta-frontend.out.log
environment=PYTHONPATH="/opt/mbta-agentcy"
S2

# Start services
supervisorctl reread
supervisorctl update
supervisorctl start mbta-exchange
supervisorctl start mbta-frontend

sleep 10

echo ""
echo "=== Service Status ==="
supervisorctl status

SETUP

# Cleanup
rm "/tmp/$TARBALL_NAME" 2>/dev/null || true

echo ""
echo "🎉 ============================================================================"
echo "🎉 MBTA Exchange + UI Deployed!"
echo "🎉 ============================================================================"
echo ""
echo "📍 Instance: $INSTANCE_ID | IP: $PUBLIC_IP"
echo "🔑 Password: $ROOT_PASSWORD"
echo "🔑 SSH Key: $SSH_KEY_LABEL"
echo ""
echo "🌐 Endpoints:"
echo "   Exchange:  http://$PUBLIC_IP:8100"
echo "   Frontend:  http://$PUBLIC_IP:3000"
echo "   Jaeger:    http://$PUBLIC_IP:16686"
echo "   Grafana:   http://$PUBLIC_IP:3001"
echo ""
echo "🔗 Connected to Agents:"
echo "   Agents IP: $AGENTS_IP"
echo "   Alerts:     http://$AGENTS_IP:8001"
echo "   Planner:    http://$AGENTS_IP:8002"
echo "   StopFinder: http://$AGENTS_IP:8003"
echo ""
echo "🧪 Test Commands:"
echo "   curl http://$PUBLIC_IP:8100/"
echo "   curl -X POST http://$PUBLIC_IP:8100/chat -d '{\"query\":\"Red Line delays?\"}'"
echo "   http://$PUBLIC_IP:3000"
echo ""
echo "📝 SSH Access:"
echo "   ssh -i $SSH_KEY_LABEL root@$PUBLIC_IP"
echo ""
echo "🛑 Delete Instance:"
echo "   linode-cli linodes delete $INSTANCE_ID"
echo ""

# Save deployment info
cat > exchange-deployment-info.txt << EOF
MBTA Exchange + UI Deployment
==============================
Deployed: $(date)
Instance ID: $INSTANCE_ID
Public IP: $PUBLIC_IP
Root Password: $ROOT_PASSWORD
SSH Key: $SSH_KEY_LABEL

Endpoints:
- Exchange: http://$PUBLIC_IP:8100
- Frontend: http://$PUBLIC_IP:3000
- Jaeger: http://$PUBLIC_IP:16686

Connected Agents:
- Agents IP: $AGENTS_IP
- Alerts: http://$AGENTS_IP:8001
- Planner: http://$AGENTS_IP:8002
- StopFinder: http://$AGENTS_IP:8003
EOF

echo "💾 Deployment info saved: exchange-deployment-info.txt"
echo ""