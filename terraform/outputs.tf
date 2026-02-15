# ============================================================
# Outputs for MBTA Winter 2026 Terraform configuration
# ============================================================

output "LKE_CLUSTER_ID" {
  description = "LKE Cluster ID"
  value       = linode_lke_cluster.mbta.id
}

output "LKE_CLUSTER_LABEL" {
  description = "LKE Cluster Label"
  value       = linode_lke_cluster.mbta.label
}

output "LKE_CLUSTER_KUBECONFIG" {
  description = "Base64-encoded kubeconfig for the LKE cluster"
  value       = linode_lke_cluster.mbta.kubeconfig
  sensitive   = true
}

output "KUBECONFIG_PATH" {
  description = "Path to the generated kubeconfig file"
  value       = local_file.kubeconfig.filename
}

output "LKE_API_ENDPOINTS" {
  description = "Kubernetes API endpoints"
  value       = linode_lke_cluster.mbta.api_endpoints
}

output "LKE_CLUSTER_STATUS" {
  description = "LKE Cluster status"
  value       = linode_lke_cluster.mbta.status
}
