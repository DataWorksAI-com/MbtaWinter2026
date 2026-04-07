# Federated MBTA Demo Runbook

**Presentation goal:** Show that MBTA agents distributed across three independent registries are discovered and orchestrated as one federated system through the Switchboard.

## Target Topology

| Agent | Registry | Protocol |
|---|---|---|
| `mbta-stopfinder` | External MIT-NANDA (`https://nest.projectnanda.org`) | REST |
| `mbta-alerts` | Internal Northeastern NANDA | REST |
| `mbta-planner` | Internal Northeastern ADS | gRPC |

## Demo Query

> "I need to get from South Station to Cambridge. Show me accessible routes and check if there are any delays on the Red Line."

Expected behavior: Switchboard returns candidates from at least two registries; Exchange selects and invokes agents from multiple registries in one flow.

---

## 1. Pre-flight Checklist

Before starting, confirm:

- [ ] `KUBECONFIG` points at the LKE cluster (`export KUBECONFIG=$(pwd)/terraform/kubeconfig.yaml`)
- [ ] `ENABLE_FEDERATION: "true"` in `k8s/configmap.yaml`
- [ ] `NEU_REGISTRY_URL` set in ConfigMap (Northeastern NANDA endpoint)
- [ ] `AGNTCY_ADS_GRPC_ADDRESS` set in ConfigMap (internal ADS gRPC address)
- [ ] `stopfinder.agent.mitdataworksai.com` resolves and returns 200 (see §3)
- [ ] `mbta-stopfinder` is registered in MIT-NANDA (`https://nest.projectnanda.org`)
- [ ] `mbta-alerts` is registered in Northeastern NANDA
- [ ] `mbta-planner` is registered in internal ADS

---

## 2. Startup Order

Services must come up in this order due to dependencies:

```bash
# 1. Verify all pods are running
kubectl -n mbta get pods

# 2. If any pods are not Ready, apply manifests
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/secrets.yaml   # requires secrets.yaml to exist — not committed
kubectl apply -f k8s/

# 3. Wait for registry and agents before starting exchange
kubectl -n mbta rollout status deployment/registry
kubectl -n mbta rollout status deployment/alerts-agent
kubectl -n mbta rollout status deployment/planner-agent
kubectl -n mbta rollout status deployment/stopfinder-agent

# 4. Register agents (if not already registered)
kubectl -n mbta delete job register-agents --ignore-not-found
kubectl apply -f k8s/register-agents-job.yaml
kubectl -n mbta logs job/register-agents -f

# 5. Restart exchange AFTER registry is ready (avoids startup race condition)
kubectl -n mbta rollout restart deployment/exchange
kubectl -n mbta logs deploy/exchange --tail=20
# Expected: ✅ Registry validation passed - A2A path ready
```

---

## 3. Health Checks

Run all of these before starting the demo. Every check must pass.

### In-cluster services (port-forward registry first)

```bash
kubectl -n mbta port-forward svc/registry 6900:6900 &
```

| Service | Check | Expected |
|---|---|---|
| Registry | `curl http://localhost:6900/health` | `{"status":"healthy"}` |
| Alerts | `kubectl -n mbta exec deploy/exchange -- curl -s http://alerts-agent:8001/health` | `{"status":"ok"}` |
| Planner | `kubectl -n mbta exec deploy/exchange -- curl -s http://planner-agent:8002/health` | `{"status":"ok"}` |
| StopFinder | `kubectl -n mbta exec deploy/exchange -- curl -s http://stopfinder-agent:8003/health` | `{"status":"ok"}` |
| Exchange | `kubectl -n mbta exec deploy/exchange -- curl -s http://localhost:8100/` | HTTP 200 |

### Public endpoint

```bash
curl https://stopfinder.agent.mitdataworksai.com/health
# Expected: {"status":"ok"}
```

---

## 4. Federation Validation

Verify all three registries are reachable through the Switchboard.

```bash
# Registry must still be port-forwarded on 6900

# 4a. Check all configured registries are connected
curl http://localhost:6900/switchboard/registries

# 4b. Verify each agent resolves through its assigned registry
curl "http://localhost:6900/switchboard/lookup/@nanda:mbta-stopfinder"
curl "http://localhost:6900/switchboard/lookup/@neu:mbta-alerts"
curl "http://localhost:6900/switchboard/lookup/@agntcy:mbta-planner"

# 4c. Run full diagnostics — all three must show reachable_found
curl "http://localhost:6900/switchboard/diagnostics?agent=mbta-stopfinder"
curl "http://localhost:6900/switchboard/diagnostics?agent=mbta-alerts"
curl "http://localhost:6900/switchboard/diagnostics?agent=mbta-planner"
```

Each diagnostics call must return `"reachable_found"` for the assigned registry. Any other status (`upstream_unavailable`, `reachable_empty_result`, `reachable_schema_mismatch`) means the federation is not ready.

---

## 5. Demo Execution

```bash
# Port-forward exchange for the demo
kubectl -n mbta port-forward svc/exchange 8100:8100 &

# Run the demo query
curl -s -X POST http://localhost:8100/chat \
  -H "Content-Type: application/json" \
  -d '{"query": "I need to get from South Station to Cambridge. Show me accessible routes and check if there are any delays on the Red Line."}'
```

Or use the chat UI at `https://mbta.mitdataworksai.com`.

**What to show the audience:** In the System Internals panel (right side of UI), point out:
- Execution Path shows agents from multiple registries being selected
- Orchestration Logic shows the Switchboard driving discovery, not a single catalog
- Agents Called shows stopfinder + alerts + planner all firing in one query

---

## 6. Fallback Plan

**If internal ADS is unavailable (planner not resolving):**
- Demo federation across MIT-NANDA (stopfinder) and Northeastern NANDA (alerts) only
- Use a two-agent query: *"Check for delays on the Red Line and find the stops between South Station and Central."*
- Note to audience: ADS integration is implemented but excluded from this demo for reliability

**If stopfinder public URL is down:**
- Check `https://stopfinder.agent.mitdataworksai.com/health` — if nginx 404, the ingress rule needs to be applied (`kubectl apply -f k8s/ingress.yaml`)
- If DNS issue, contact Sharanya (has Linode DNS access)

**If exchange shows A2A path unavailable:**
```bash
kubectl -n mbta rollout restart deployment/exchange
# Wait 20 seconds, then retry
```

**If any agent pod is crash-looping:**
```bash
kubectl -n mbta describe pod <pod-name>
kubectl -n mbta logs <pod-name> -c <container-name> --previous
```

---

## 7. Post-Demo Cleanup

```bash
# Kill port-forwards
kill $(lsof -ti:6900) $(lsof -ti:8100) 2>/dev/null || true
```
