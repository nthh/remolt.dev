terraform {
  required_version = ">= 1.5"

  required_providers {
    vultr = {
      source  = "vultr/vultr"
      version = "~> 2.21"
    }
    local = {
      source  = "hashicorp/local"
      version = "~> 2.5"
    }
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

provider "vultr" {
  api_key = var.vultr_api_key
}

provider "cloudflare" {
  api_token = var.cloudflare_api_token
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

# --- Cloudflare Tunnel ---

resource "cloudflare_zero_trust_tunnel_cloudflared" "remolt" {
  account_id = var.cloudflare_account_id
  name       = "remolt-${var.environment}"
  secret     = base64encode(random_id.tunnel_secret.b64_std)
}

resource "random_id" "tunnel_secret" {
  byte_length = 32
}

resource "cloudflare_zero_trust_tunnel_cloudflared_config" "remolt" {
  account_id = var.cloudflare_account_id
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.remolt.id

  config {
    ingress_rule {
      hostname = var.domain
      service  = "http://remolt-server:8080"
    }
    ingress_rule {
      service = "http_status:404"
    }
  }
}

resource "cloudflare_dns_record" "remolt" {
  zone_id = var.cloudflare_zone_id
  name    = var.domain
  type    = "CNAME"
  content = cloudflare_zero_trust_tunnel_cloudflared.remolt.cname
  proxied = true
}

