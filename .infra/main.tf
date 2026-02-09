terraform {
  required_version = ">= 1.5"

  required_providers {
    vultr = {
      source  = "vultr/vultr"
      version = "~> 2.21"
    }
  }
}

provider "vultr" {
  api_key = var.vultr_api_key
}

# --- VKE Cluster ---

resource "vultr_kubernetes" "remolt" {
  region  = var.region
  label   = "remolt-${var.environment}"
  version = var.k8s_version

  node_pools {
    node_quantity = var.node_count
    plan          = var.node_plan
    label         = "remolt-workers"
    auto_scaler   = true
    min_nodes     = var.node_count
    max_nodes     = var.node_max
  }
}

# --- Container Registry ---

resource "vultr_container_registry" "remolt" {
  name   = "remolt-${var.environment}"
  region = var.region
  plan   = "start_up"
  public = false
}
