#!/bin/bash

# ============================================================================
# MBTA Observability Stack - Linode Deployment
# ============================================================================
# Deploys Jaeger, Grafana, ClickHouse, and OTEL Collector
# ============================================================================

set -e

REGION="${1:-us-east}"
INSTANCE_TYPE="${2:-g6-nanode-1}"
ROOT_PASSWORD="${3:-}"

if [ -z "$ROOT_PASSWORD" ]; then
    ROOT_PASSWORD=$(openssl rand -base64 32 | tr -d "=+/" | cut -c1-25)
fi

FIREWALL_LABEL="mbta-observability-firewall"
SSH_KEY_LABEL="mbta-observability-key"
IMAGE_ID="linode/ubuntu22.04"
DEPLOYMENT_ID=$(date +%Y%m%d-%H%M%S)

echo "📊 MBTA Observability Stack Deployment"
echo "Deployment ID: $DEPLOYMENT_ID"
echo ""

# [1/8] Check Linode CLI
echo "[1/8] Checking Linode CLI..."
if ! linode-cli --version >/dev/null 2>&1; then
    echo "❌ Linode CLI not installed"
    exit 1
fi
echo "✅ Linode CLI ready"

# [2/8] Setup firewall
echo "[2/8] Setting up firewall..."
FIREWALL_ID=$(linode-cli firewalls list --text --no-headers --format="id,label" | grep "$FIREWALL_LABEL" | cut -f1 || echo "")

# Firewall rules for observability stack
INBOUND_RULES='[
    {"protocol": "TCP", "ports": "22", "addresses": {"ipv4": ["0.0.0.0/0"]}, "action": "ACCEPT", "label": "SSH"},
    {"protocol": "TCP", "ports": "4317", "addresses": {"ipv4": ["0.0.0.0/0"]}, "action": "ACCEPT", "label": "OTLP-gRPC"},
    {"protocol": "TCP", "ports": "4318", "addresses": {"ipv4": ["0.0.0.0/0"]}, "action": "ACCEPT", "label": "OTLP-HTTP"},
    {"protocol": "TCP", "ports": "16686", "addresses": {"ipv4": ["0.0.0.0/0"]}, "action": "ACCEPT", "label": "Jaeger-UI"},
    {"protocol": "TCP", "ports": "3001", "addresses": {"ipv4": ["0.0.0.0/0"]}, "action": "ACCEPT", "label": "Grafana"},
    {"protocol": "TCP", "ports": "8123", "addresses": {"ipv4": ["0.0.0.0/0"]}, "action": "ACCEPT", "label": "ClickHouse-HTTP"},
    {"protocol": "TCP", "ports": "9000", "addresses": {"ipv4": ["0.0.0.0/0"]}, "action": "ACCEPT", "label": "ClickHouse-Native"}
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

# [3/8] Setup SSH key
echo "[3/8] Setting up SSH key..."
if [ ! -f "${SSH_KEY_LABEL}.pub" ]; then
    ssh-keygen -t rsa -b 4096 -f "$SSH_KEY_LABEL" -N "" -C "observability-$DEPLOYMENT_ID" >/dev/null 2>&1
    echo "✅ Generated SSH key"
else
    echo "✅ Using existing SSH key"
fi

# [4/8] Launch instance
echo "[4/8] Launching Linode..."
INSTANCE_ID=$(linode-cli linodes create \
    --type "$INSTANCE_TYPE" \
    --region "$REGION" \
    --image "$IMAGE_ID" \
    --label "mbta-observability-$DEPLOYMENT_ID" \
    --tags "MBTA-Observability" \
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

# [5/8] Wait for SSH
echo "[5/8] Waiting for SSH..."
for i in {1..60}; do
    if ssh -i "$SSH_KEY_LABEL" -o StrictHostKeyChecking=no -o ConnectTimeout=5 \
        "root@$PUBLIC_IP" "echo ready" >/dev/null 2>&1; then
        echo "✅ SSH ready"
        break
    fi
    [ $i -eq 60 ] && { echo "❌ SSH timeout"; exit 1; }
    sleep 5
done

# [6/8] Install Docker
echo "[6/8] Installing Docker and Docker Compose (2-3 min)..."
ssh -i "$SSH_KEY_LABEL" -o StrictHostKeyChecking=no "root@$PUBLIC_IP" bash << 'DOCKER_INSTALL'
set -e
export DEBIAN_FRONTEND=noninteractive

apt-get update -y >/dev/null 2>&1

# Install Docker
curl -fsSL https://get.docker.com | sh >/dev/null 2>&1

# Install Docker Compose
curl -SL https://github.com/docker/compose/releases/download/v2.24.0/docker-compose-linux-x86_64 \
    -o /usr/local/bin/docker-compose >/dev/null 2>&1
chmod +x /usr/local/bin/docker-compose

# Verify
docker --version
docker-compose --version

echo "✅ Docker installed"
DOCKER_INSTALL

# [7/8] Deploy observability stack
echo "[7/8] Deploying observability stack (2-3 min)..."

ssh -i "$SSH_KEY_LABEL" -o StrictHostKeyChecking=no "root@$PUBLIC_IP" bash << 'OBSERVABILITY_SETUP'
set -e

# Create directory
mkdir -p /opt/observability
cd /opt/observability

# Create docker-compose.yml
cat > docker-compose.yml << 'COMPOSE_EOF'
version: '3.8'

services:
  jaeger:
    image: jaegertracing/all-in-one:1.52
    container_name: mbta-jaeger
    restart: unless-stopped
    ports:
      - "16686:16686"  # Jaeger UI
      - "14250:14250"  # gRPC receiver from OTEL Collector
    environment:
      - SPAN_STORAGE_TYPE=badger
      - BADGER_EPHEMERAL=false
      - BADGER_DIRECTORY_VALUE=/badger/data
      - BADGER_DIRECTORY_KEY=/badger/key
      - COLLECTOR_OTLP_ENABLED=true
    volumes:
      - jaeger-data:/badger
    networks:
      - observability

  otel-collector:
    image: otel/opentelemetry-collector-contrib:0.91.0
    container_name: mbta-otel-collector
    restart: unless-stopped
    ports:
      - "0.0.0.0:4317:4317"   # OTLP gRPC - accept external connections
      - "0.0.0.0:4318:4318"   # OTLP HTTP - accept external connections
    command: ["--config=/etc/otel-collector-config.yaml"]
    volumes:
      - ./otel-collector-config.yaml:/etc/otel-collector-config.yaml
    depends_on:
      - jaeger
      - clickhouse
    networks:
      - observability

  clickhouse:
    image: clickhouse/clickhouse-server:23.12
    container_name: mbta-clickhouse
    restart: unless-stopped
    ports:
      - "0.0.0.0:8123:8123"   # HTTP interface - accept external connections
      - "0.0.0.0:9000:9000"   # Native protocol - accept external connections
    environment:
      - CLICKHOUSE_USER=default
      - CLICKHOUSE_PASSWORD=
      - CLICKHOUSE_DB=mbta_metrics
    volumes:
      - clickhouse-data:/var/lib/clickhouse
    networks:
      - observability

  grafana:
    image: grafana/grafana:10.2.3
    container_name: mbta-grafana
    restart: unless-stopped
    ports:
      - "3001:3000"  # Grafana UI
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin
      - GF_INSTALL_PLUGINS=grafana-clickhouse-datasource
    volumes:
      - grafana-data:/var/lib/grafana
    depends_on:
      - clickhouse
    networks:
      - observability

volumes:
  jaeger-data:
  clickhouse-data:
  grafana-data:

networks:
  observability:
    driver: bridge
COMPOSE_EOF

# Create OTEL collector config
cat > otel-collector-config.yaml << 'OTEL_EOF'
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318
        cors:
          allowed_origins:
            - "*"

processors:
  batch:
    timeout: 1s
    send_batch_size: 50
  
  memory_limiter:
    check_interval: 1s
    limit_mib: 512
  
  attributes:
    actions:
      - key: deployment.environment
        value: production
        action: insert
      - key: service.namespace
        value: mbta-agentcy
        action: insert

exporters:
  # Export traces to Jaeger
  otlp/jaeger:
    endpoint: jaeger:4317
    tls:
      insecure: true
  
  # Export to ClickHouse for long-term storage and analytics
  clickhouse:
    endpoint: tcp://clickhouse:9000
    database: mbta_metrics
    ttl: 72h
    traces_table_name: otel_traces
    metrics_table_name: otel_metrics
    logs_table_name: otel_logs
    timeout: 5s
    retry_on_failure:
      enabled: true
      initial_interval: 5s
      max_interval: 30s
      max_elapsed_time: 300s
  
  # Debug logging
  logging:
    loglevel: info

service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [memory_limiter, batch, attributes]
      exporters: [otlp/jaeger, clickhouse, logging]
    
    metrics:
      receivers: [otlp]
      processors: [memory_limiter, batch, attributes]
      exporters: [clickhouse, logging]
    
    logs:
      receivers: [otlp]
      processors: [memory_limiter, batch, attributes]
      exporters: [clickhouse, logging]
OTEL_EOF

echo "✅ Configuration files created"

# Start services
docker-compose up -d

echo "   Waiting for containers to start..."
sleep 15

# Verify all containers are running
docker ps --format "table {{.Names}}\t{{.Status}}"

echo "✅ Observability stack deployed"
OBSERVABILITY_SETUP

# [8/8] Verify deployment
echo "[8/8] Verifying deployment..."

CONTAINER_COUNT=$(ssh -i "$SSH_KEY_LABEL" "root@$PUBLIC_IP" "docker ps | grep -c mbta-" || echo "0")
if [ "$CONTAINER_COUNT" -eq 4 ]; then
    echo "✅ All 4 containers running"
else
    echo "⚠️  Warning: Only $CONTAINER_COUNT containers running (expected 4)"
fi

echo ""
echo "🎉 ============================================================================"
echo "🎉 MBTA Observability Stack Deployed!"
echo "🎉 ============================================================================"
echo ""
echo "📍 Instance: $INSTANCE_ID | IP: $PUBLIC_IP"
echo "🔑 Password: $ROOT_PASSWORD"
echo "🔑 SSH Key: $SSH_KEY_LABEL"
echo ""
echo "🌐 Access URLs:"
echo "   Jaeger UI:    http://$PUBLIC_IP:16686"
echo "   Grafana:      http://$PUBLIC_IP:3001 (admin/admin)"
echo "   ClickHouse:   http://$PUBLIC_IP:8123"
echo ""
echo "📡 OTLP Endpoints:"
echo "   gRPC:         http://$PUBLIC_IP:4317"
echo "   HTTP:         http://$PUBLIC_IP:4318"
echo ""
echo "🧪 Test Commands:"
echo "   curl http://$PUBLIC_IP:16686"
echo "   curl http://$PUBLIC_IP:4318/v1/traces -i"
echo ""
echo "📝 SSH Access:"
echo "   ssh -i $SSH_KEY_LABEL root@$PUBLIC_IP"
echo ""
echo "🔧 Docker Commands:"
echo "   ssh -i $SSH_KEY_LABEL root@$PUBLIC_IP 'cd /opt/observability && docker-compose ps'"
echo "   ssh -i $SSH_KEY_LABEL root@$PUBLIC_IP 'cd /opt/observability && docker-compose logs -f'"
echo ""
echo "⚠️  NEXT STEPS:"
echo "   1. Update Exchange Server .env:"
echo "      OTEL_ENDPOINT=http://$PUBLIC_IP:4317"
echo ""
echo "   2. Update Agents Server .env:"
echo "      OTEL_ENDPOINT=http://$PUBLIC_IP:4317"
echo ""
echo "   3. Restart services on both servers:"
echo "      supervisorctl restart all"
echo ""
echo "   4. Verify traces in Jaeger UI:"
echo "      http://$PUBLIC_IP:16686"
echo ""
echo "🛑 Delete Instance:"
echo "   linode-cli linodes delete $INSTANCE_ID"
echo ""

# Save deployment info
cat > observability-deployment-info.txt << EOF
MBTA Observability Deployment
==============================
Deployed: $(date)
Instance ID: $INSTANCE_ID
Public IP: $PUBLIC_IP
Root Password: $ROOT_PASSWORD
SSH Key: $SSH_KEY_LABEL

Access URLs:
- Jaeger UI: http://$PUBLIC_IP:16686
- Grafana: http://$PUBLIC_IP:3001 (admin/admin)
- ClickHouse: http://$PUBLIC_IP:8123

OTLP Endpoints:
- gRPC: http://$PUBLIC_IP:4317
- HTTP: http://$PUBLIC_IP:4318

Update Application Servers:
---------------------------
Add to /opt/mbta-agentcy/.env on BOTH servers:
OTEL_ENDPOINT=http://$PUBLIC_IP:4317

Then restart:
supervisorctl restart all
EOF

echo "💾 Deployment info saved: observability-deployment-info.txt"
echo ""