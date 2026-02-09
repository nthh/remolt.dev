output "cluster_id" {
  description = "VKE cluster ID"
  value       = vultr_kubernetes.remolt.id
}

output "cluster_endpoint" {
  description = "Kubernetes API endpoint"
  value       = vultr_kubernetes.remolt.endpoint
  sensitive   = true
}

output "kubeconfig" {
  description = "Base64-encoded kubeconfig"
  value       = vultr_kubernetes.remolt.kube_config
  sensitive   = true
}

resource "local_file" "kubeconfig" {
  content         = base64decode(vultr_kubernetes.remolt.kube_config)
  filename        = "${path.module}/kubeconfig-${var.environment}.yaml"
  file_permission = "0600"
}
