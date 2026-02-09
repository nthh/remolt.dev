output "cluster_id" {
  description = "VKE cluster ID"
  value       = vultr_kubernetes.remolt.id
}

output "cluster_endpoint" {
  description = "Kubernetes API endpoint"
  value       = vultr_kubernetes.remolt.endpoint
}

output "kubeconfig" {
  description = "Base64-encoded kubeconfig"
  value       = vultr_kubernetes.remolt.kube_config
  sensitive   = true
}

output "registry_urn" {
  description = "Container registry URN for docker login"
  value       = vultr_container_registry.remolt.urn
}

output "registry_root_uri" {
  description = "Registry URI for image tagging"
  value = join("/", [
    "${vultr_container_registry.remolt.storage.0.region.0.name}.vultrcr.com",
    vultr_container_registry.remolt.name,
  ])
}
