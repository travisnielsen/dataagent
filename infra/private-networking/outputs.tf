output "azure_tenant_id" {
  description = "Azure tenant ID used by this deployment."
  value       = data.azurerm_client_config.current.tenant_id
}

output "azure_subscription_id" {
  description = "Azure subscription ID used by this deployment."
  value       = data.azurerm_subscription.current.subscription_id
}

output "resource_group_name" {
  description = "Private networking resource group name."
  value       = azurerm_resource_group.private_rg.name
}

output "azure_location" {
  description = "Azure location for resources in this private networking deployment."
  value       = azurerm_resource_group.private_rg.location
}

output "virtual_network_id" {
  description = "Virtual network resource ID."
  value       = azurerm_virtual_network.private_vnet.id
}

output "subnet_ids" {
  description = "Subnet resource IDs keyed by logical name."
  value = {
    private_endpoints = azurerm_subnet.private_endpoints.id
    application       = azurerm_subnet.application.id
    container_apps    = azurerm_subnet.container_apps.id
    ai_agent_services = azurerm_subnet.ai_agent_services.id
    utility           = azurerm_subnet.utility.id
    azure_bastion     = azurerm_subnet.azure_bastion.id
    data              = azurerm_subnet.data.id
  }
}

output "utility_vm_name" {
  description = "Utility VM name."
  value       = azurerm_windows_virtual_machine.utility.name
}

output "utility_vm_private_ip" {
  description = "Utility VM private IP address."
  value       = azurerm_network_interface.utility_vm.private_ip_address
}

output "utility_vm_id" {
  description = "Utility VM resource ID."
  value       = azurerm_windows_virtual_machine.utility.id
}

output "bastion_name" {
  description = "Azure Bastion host name."
  value       = azurerm_bastion_host.main.name
}

output "bastion_id" {
  description = "Azure Bastion host resource ID."
  value       = azurerm_bastion_host.main.id
}

output "bastion_public_ip" {
  description = "Public IP address for Azure Bastion."
  value       = azurerm_public_ip.bastion.ip_address
}

output "private_dns_zone_ids" {
  description = "Private DNS zone resource IDs keyed by zone name."
  value       = { for zone_name, zone in azurerm_private_dns_zone.this : zone_name => zone.id }
}

output "private_endpoint_ids" {
  description = "Private endpoint IDs keyed by endpoint name."
  value       = { for endpoint_name, endpoint in azurerm_private_endpoint.this : endpoint_name => endpoint.id }
}

output "appinsights_connection_string" {
  description = "Application Insights connection string"
  value       = module.application_insights.connection_string
  sensitive   = true
}

output "ai_foundry_id" {
  description = "AI Foundry account resource ID"
  value       = module.ai_foundry.ai_foundry_id
}

output "ai_foundry_project_id" {
  description = "AI Foundry project resource ID"
  value       = module.ai_foundry.ai_foundry_project_id
}

output "container_app_identity_client_id" {
  description = "Container App managed identity client ID"
  value       = azurerm_user_assigned_identity.api_identity.client_id
}

output "container_app_identity_name" {
  description = "Container App managed identity name"
  value       = azurerm_user_assigned_identity.api_identity.name
}

output "container_registry_login_server" {
  description = "Container Registry login server"
  value       = module.container_registry.resource.login_server
}

output "container_registry_name" {
  description = "Container Registry resource name"
  value       = element(reverse(split("/", module.container_registry.resource_id)), 0)
}

output "acr_agent_pool_name" {
  description = "Container Registry agent pool name for private ACR Tasks builds; null in public mode"
  value       = local.acr_is_private ? azurerm_container_registry_agent_pool.acr_tasks[0].name : null
}

output "acr_build_mode" {
  description = "Configured ACR build mode (public or private)."
  value       = var.acr_build_mode
}

output "container_app_environment_name" {
  description = "Container Apps environment name"
  value       = element(reverse(split("/", module.container_app_environment.resource_id)), 0)
}

output "storage_account_name" {
  description = "Storage account name used for NL2SQL assets"
  value       = module.ai_storage.name
}

output "search_service_name" {
  description = "AI Search service name"
  value       = module.ai_search.resource.name
}

output "search_storage_shared_private_link_name" {
  description = "AI Search shared private link name targeting Storage Blob"
  value       = azurerm_search_shared_private_link_service.ai_search_storage_blob.name
}

output "search_storage_shared_private_link_status" {
  description = "Provisioning status of AI Search shared private link targeting Storage Blob"
  value       = azurerm_search_shared_private_link_service.ai_search_storage_blob.status
}

output "ai_foundry_account_name" {
  description = "AI Foundry account name"
  value       = element(reverse(split("/", module.ai_foundry.ai_foundry_id)), 0)
}

output "sql_database_name" {
  description = "Azure SQL database name"
  value       = var.sql_database_name
}

output "container_app_name" {
  description = "Container App name for backend API"
  value       = azurerm_container_app.api.name
}

output "container_app_url" {
  description = "Container App API URL"
  value       = "https://${azurerm_container_app.api.ingress[0].fqdn}"
}

output "static_web_app_name" {
  description = "Azure Static Web App name for frontend hosting"
  value       = azurerm_static_web_app.frontend.name
}

output "static_web_app_url" {
  description = "Azure Static Web App URL"
  value       = "https://${azurerm_static_web_app.frontend.default_host_name}"
}

output "sql_server_name" {
  description = "Azure SQL server name"
  value       = module.sql_server.resource.name
  sensitive   = true
}
