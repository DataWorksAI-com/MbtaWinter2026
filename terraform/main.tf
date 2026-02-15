# ============================================================
# Terraform configuration for MBTA Winter 2026
# Provisions Akamai/Linode Kubernetes Engine (LKE) cluster
# ============================================================

terraform {
  required_version = ">= 1.0"

  required_providers {
    linode = {
      source  = "linode/linode"
      version = "~> 2.0"
    }
  }
}

provider "linode" {
  token = var.linode_token
}

# ─────────────────────────────────────────────
# LKE Cluster
# ─────────────────────────────────────────────
resource "linode_lke_cluster" "mbta" {
  label       = var.cluster_label
  k8s_version = var.k8s_version
  region      = var.region

  control_plane {
    high_availability = var.enable_ha_control_plane
  }

  tags = var.tags

  pool {
    type  = var.lke_node_type
    count = var.lke_node_count

    autoscaler {
      min = var.lke_autoscaler_min
      max = var.lke_autoscaler_max
    }
  }
}

# ─────────────────────────────────────────────
# Write kubeconfig to local file
# ─────────────────────────────────────────────
resource "local_file" "kubeconfig" {
  content         = base64decode(linode_lke_cluster.mbta.kubeconfig)
  filename        = "${path.module}/kubeconfig.yaml"
  file_permission = "0600"
}
