variable "vultr_api_key" {
  description = "Vultr API key"
  type        = string
  sensitive   = true
}

variable "environment" {
  description = "Environment name (dev, staging, prod)"
  type        = string
  default     = "dev"
}

variable "region" {
  description = "Vultr region (e.g. ewr = New Jersey, lax = LA, sjc = Silicon Valley)"
  type        = string
  default     = "ewr"
}

variable "k8s_version" {
  description = "Kubernetes version"
  type        = string
  default     = "v1.32.9+3"
}

variable "node_plan" {
  description = "Vultr plan for worker nodes"
  type        = string
  default     = "vc2-2c-4gb"
}

variable "node_count" {
  description = "Initial/minimum number of worker nodes"
  type        = number
  default     = 2
}

variable "node_max" {
  description = "Maximum number of worker nodes (autoscaler)"
  type        = number
  default     = 20
}
