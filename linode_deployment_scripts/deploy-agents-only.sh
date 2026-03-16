#!/bin/bash

# ============================================================================
# MBTA Agents - Separate Deployment (No Registry)
# ============================================================================
# Deploys only the 3 A2A agents to a separate Linode instance
# Based on proven working deployment patterns
# ============================================================================

set -e

MBTA_API_KEY="$1"
OPENAI_API_KEY="$2"
REGION="${3:-us-east}"
INSTANCE_TYPE="${4:-g6-standard-2}"
ROOT_PASSWORD="${5:-}"

if [ -z "$MBTA_API_KEY" ] || [ -z "$OPENAI_API_KEY" ]; then
    echo "❌ Usage: $0 <MBTA_KEY> <OPENAI_KEY> [REGION] [INSTANCE_TYPE]"
    echo ""
    echo "Example:"
    echo "  bash deploy-agents-only.sh \"c845...\" \"sk-proj-...\""
    exit 1
fi

if [ -z "$ROOT_PASSWORD" ]; then
    ROOT_PASSWORD=$(openssl rand -base64 32 | tr -d "=+/" | cut -c1-25)
fi

FIREWALL_LABEL="mbta-agents-firewall"
SSH_KEY_LABEL="mbta-agents-key"
IMAGE_ID="linode/ubuntu22.04"
DEPLOYMENT_ID=$(date +%Y%m%d-%H%M%S)

echo "🚇 MBTA Agents - Separate Deployment"
echo "Deployment ID: $DEPLOYMENT_ID"
echo ""

# [1/9] Check Linode CLI
echo "[1/9] Checking Linode CLI..."
if ! linode-cli --version >/dev/null 2>&1; then
    echo "❌ Linode CLI not installed"
    exit 1
fi
echo "✅ Linode CLI ready"

# [2/9] Package agents ONLY
echo "[2/9] Packaging agents..."

if [ ! -d "agents" ]; then
    echo "❌ agents/ directory not found! Run from mbta/ folder"
    exit 1
fi

TARBALL_NAME="mbta-agents-${DEPLOYMENT_ID}.tar.gz"

# Package ONLY agents folder
tar -czf "/tmp/$TARBALL_NAME" \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='*.pyo' \
    --exclude='.pytest_cache' \
    agents/ \
    2>/dev/null || true

TARBALL_SIZE=$(du -h "/tmp/$TARBALL_NAME" | cut -f1)
echo "✅ Packaged: $TARBALL_SIZE"

# [3/9] Setup firewall
echo "[3/9] Setting up firewall..."
FIREWALL_ID=$(linode-cli firewalls list --text --no-headers --format="id,label" | grep "$FIREWALL_LABEL" | cut -f1 || echo "")

INBOUND_RULES='[
    {"protocol": "TCP", "ports": "22", "addresses": {"ipv4": ["0.0.0.0/0"]}, "action": "ACCEPT"},
    {"protocol": "TCP", "ports": "8001", "addresses": {"ipv4": ["0.0.0.0/0"]}, "action": "ACCEPT"},
    {"protocol": "TCP", "ports": "8002", "addresses": {"ipv4": ["0.0.0.0/0"]}, "action": "ACCEPT"},
    {"protocol": "TCP", "ports": "8003", "addresses": {"ipv4": ["0.0.0.0/0"]}, "action": "ACCEPT"}
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
    ssh-keygen -t rsa -b 4096 -f "$SSH_KEY_LABEL" -N "" -C "mbta-agents-$DEPLOYMENT_ID" >/dev/null 2>&1
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
    --label "mbta-agents-$DEPLOYMENT_ID" \
    --tags "MBTA-Agents" \
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
echo "[7/9] Uploading agents ($TARBALL_SIZE)..."

if scp -i "$SSH_KEY_LABEL" -o StrictHostKeyChecking=no -o ServerAliveInterval=30 \
    "/tmp/$TARBALL_NAME" "root@$PUBLIC_IP:/tmp/"; then
    echo "✅ Upload successful!"
else
    echo "❌ Upload failed"
    exit 1
fi

# [8/9] Install packages
echo "[8/9] Installing system packages (3-5 min)..."
ssh -i "$SSH_KEY_LABEL" -o StrictHostKeyChecking=no "root@$PUBLIC_IP" bash << 'PACKAGES'
set -e
export DEBIAN_FRONTEND=noninteractive

apt-get update -y >/dev/null 2>&1
apt-get install -y software-properties-common >/dev/null 2>&1

add-apt-repository -y ppa:deadsnakes/ppa >/dev/null 2>&1
apt-get update -y >/dev/null 2>&1
apt-get install -y python3.11 python3.11-venv python3.11-dev python3-pip supervisor >/dev/null 2>&1

echo "✅ Packages installed"
PACKAGES

# [9/9] Setup services
echo "[9/9] Configuring agents (3-5 min)..."

ssh -i "$SSH_KEY_LABEL" -o StrictHostKeyChecking=no "root@$PUBLIC_IP" bash << SETUP
set -e

cd /opt
mkdir -p mbta-agents
cd mbta-agents

tar -xzf /tmp/$TARBALL_NAME
rm /tmp/$TARBALL_NAME

# Create Python 3.11 venv
python3.11 -m venv venv
source venv/bin/activate

pip install --upgrade pip >/dev/null 2>&1

# Install agent dependencies
pip install fastapi uvicorn httpx openai pydantic python-dotenv requests \
    opentelemetry-api opentelemetry-sdk opentelemetry-instrumentation-fastapi \
    >/dev/null 2>&1

echo "✅ Dependencies installed"

# Create .env
cat > .env << ENV
MBTA_API_KEY=$MBTA_API_KEY
OPENAI_API_KEY=$OPENAI_API_KEY
PYTHONPATH=/opt/mbta-agents
ENV

# Supervisor config - Alerts Agent
cat > /etc/supervisor/conf.d/mbta-alerts.conf << 'S1'
[program:mbta-alerts]
command=/opt/mbta-agents/venv/bin/python -m agents.alerts.main
directory=/opt/mbta-agents
autostart=true
autorestart=true
stderr_logfile=/var/log/mbta-alerts.err.log
stdout_logfile=/var/log/mbta-alerts.out.log
environment=PYTHONPATH="/opt/mbta-agents"
S1

# Supervisor config - Planner Agent
cat > /etc/supervisor/conf.d/mbta-planner.conf << 'S2'
[program:mbta-planner]
command=/opt/mbta-agents/venv/bin/python -m agents.planner.main
directory=/opt/mbta-agents
autostart=true
autorestart=true
stderr_logfile=/var/log/mbta-planner.err.log
stdout_logfile=/var/log/mbta-planner.out.log
environment=PYTHONPATH="/opt/mbta-agents"
S2

# Supervisor config - StopFinder Agent
cat > /etc/supervisor/conf.d/mbta-stopfinder.conf << 'S3'
[program:mbta-stopfinder]
command=/opt/mbta-agents/venv/bin/python -m agents.stopfinder.main
directory=/opt/mbta-agents
autostart=true
autorestart=true
stderr_logfile=/var/log/mbta-stopfinder.err.log
stdout_logfile=/var/log/mbta-stopfinder.out.log
environment=PYTHONPATH="/opt/mbta-agents"
S3

# Start services
supervisorctl reread
supervisorctl update
supervisorctl start mbta-alerts
supervisorctl start mbta-planner
supervisorctl start mbta-stopfinder

sleep 10

echo ""
echo "=== Service Status ==="
supervisorctl status

SETUP

# Cleanup
rm "/tmp/$TARBALL_NAME" 2>/dev/null || true

echo ""
echo "🎉 ============================================================================"
echo "🎉 MBTA Agents Deployed!"
echo "🎉 ============================================================================"
echo ""
echo "📍 Instance: $INSTANCE_ID | IP: $PUBLIC_IP"
echo "🔑 Password: $ROOT_PASSWORD"
echo "🔑 SSH Key: $SSH_KEY_LABEL"
echo ""
echo "🌐 Agent Endpoints:"
echo "   Alerts:     http://$PUBLIC_IP:8001"
echo "   Planner:    http://$PUBLIC_IP:8002"
echo "   StopFinder: http://$PUBLIC_IP:8003"
echo ""
echo "🧪 Test Commands:"
echo "   curl http://$PUBLIC_IP:8001/health"
echo "   curl http://$PUBLIC_IP:8002/health"
echo "   curl http://$PUBLIC_IP:8003/health"
echo ""
echo "📝 SSH Access:"
echo "   ssh -i $SSH_KEY_LABEL root@$PUBLIC_IP"
echo ""
echo "⚠️  NEXT STEP: Update exchange code with this IP!"
echo "   Edit: src/exchange_agent/stategraph_orchestrator.py"
echo "   Change FALLBACK_AGENTS URLs to: http://$PUBLIC_IP"
echo ""
echo "🛑 Delete Instance:"
echo "   linode-cli linodes delete $INSTANCE_ID"
echo ""

# Save deployment info
cat > agents-deployment-info.txt << EOF
MBTA Agents Deployment
=====================
Deployed: $(date)
Instance ID: $INSTANCE_ID
Public IP: $PUBLIC_IP
Root Password: $ROOT_PASSWORD
SSH Key: $SSH_KEY_LABEL

Endpoints:
- Alerts: http://$PUBLIC_IP:8001
- Planner: http://$PUBLIC_IP:8002
- StopFinder: http://$PUBLIC_IP:8003

Update Exchange Code:
Edit src/exchange_agent/stategraph_orchestrator.py:
FALLBACK_AGENTS = {
    "mbta-alerts": AgentConfig("mbta-alerts", "http://$PUBLIC_IP", 8001),
    "mbta-stops": AgentConfig("mbta-stops", "http://$PUBLIC_IP", 8003),
    "mbta-route-planner": AgentConfig("mbta-route-planner", "http://$PUBLIC_IP", 8002),
}
EOF

echo "💾 Deployment info saved: agents-deployment-info.txt"
echo ""