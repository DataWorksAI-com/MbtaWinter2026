# ============================================================
# Variables for MBTA Winter 2026 Terraform configuration
# ============================================================

variable "linode_token" {
  description = "Linode API Token with read/write permissions for Kubernetes"
  type        = string
  sensitive   = true
}

variable "region" {
  description = "Akamai Cloud region for all resources"
  type        = string
  default     = "us-east"

  validation {
    condition = contains([
      "ap-south",     # Singapore
      "au-mel",       # Melbourne
      "br-gru",       # Sao Paulo
      "es-mad",       # Madrid
      "eu-central",   # Frankfurt
      "fr-par",       # Paris
      "gb-lon",       # London
      "id-cgk",       # Jakarta
      "in-maa",       # Chennai
      "it-mil",       # Milan
      "jp-osa",       # Osaka
      "jp-tyo-3",     # Tokyo
      "nl-ams",       # Amsterdam
      "se-sto",       # Stockholm
      "sg-sin-2",     # Singapore 2
      "us-east",      # Newark, NJ
      "us-iad",       # Washington, DC
      "us-lax",       # Los Angeles, CA
      "us-mia",       # Miami, FL
      "us-ord",       # Chicago, IL
      "us-sea",       # Seattle, WA
      "us-southeast"  # Atlanta, GA
    ], var.region)
    error_message = "Region must support Kubernetes. See: https://www.linode.com/docs/products/compute/kubernetes/"
  }
}

variable "cluster_label" {
  description = "Label for the LKE cluster"
  type        = string
  default     = "mbta-winter-2026"

  validation {
    condition     = length(var.cluster_label) > 0 && length(var.cluster_label) <= 32
    error_message = "Cluster label must be between 1 and 32 characters."
  }
}

variable "k8s_version" {
  description = "Kubernetes version for LKE cluster"
  type        = string
  default     = "1.34"
}

variable "lke_node_type" {
  description = "Linode instance type for LKE worker nodes"
  type        = string
  default     = "g6-standard-2" # 4GB Shared
}

variable "lke_node_count" {
  description = "Initial number of nodes in the LKE cluster"
  type        = number
  default     = 3

  validation {
    condition     = var.lke_node_count >= 1 && var.lke_node_count <= 100
    error_message = "Node count must be between 1 and 100."
  }
}

variable "lke_autoscaler_min" {
  description = "Minimum number of nodes for autoscaling"
  type        = number
  default     = 3
}

variable "lke_autoscaler_max" {
  description = "Maximum number of nodes for autoscaling"
  type        = number
  default     = 5
}

variable "enable_ha_control_plane" {
  description = "Enable High Availability for Kubernetes control plane"
  type        = bool
  default     = true
}

variable "tags" {
  description = "Tags to apply to all resources"
  type        = list(string)
  default     = ["mbta", "multi-agent", "terraform"]
}
