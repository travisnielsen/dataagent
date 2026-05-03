#################################################################################
# DevOps Services
#################################################################################

module "container_registry" {
  source                        = "Azure/avm-res-containerregistry-registry/azurerm"
  name                          = replace("${local.identifier}acr", "-", "")
  resource_group_name           = azurerm_resource_group.private_rg.name
  location                      = azurerm_resource_group.private_rg.location
  sku                           = "Premium"
  zone_redundancy_enabled       = false
  public_network_access_enabled = !local.acr_is_private
  network_rule_bypass_option    = "AzureServices"
  admin_enabled                 = false
  tags                          = local.tags

  diagnostic_settings = {
    to_law = {
      name                  = "to-law"
      workspace_resource_id = module.log_analytics.resource_id
    }
  }

  private_endpoints = {
    acr = {
      subnet_resource_id            = azurerm_subnet.private_endpoints.id
      private_dns_zone_resource_ids = [azurerm_private_dns_zone.this["privatelink.azurecr.io"].id]
    }
  }

  depends_on = [time_sleep.wait_for_network_ready]
}

resource "azurerm_container_registry_agent_pool" "acr_tasks" {
  count = local.acr_is_private ? 1 : 0

  name                      = "${local.identifier}pool"
  container_registry_name   = element(reverse(split("/", module.container_registry.resource_id)), 0)
  resource_group_name       = azurerm_resource_group.private_rg.name
  location                  = azurerm_resource_group.private_rg.location
  tier                      = "S2"
  instance_count            = 2
  virtual_network_subnet_id = azurerm_subnet.application.id
  tags                      = local.tags

  depends_on = [
    module.container_registry,
    time_sleep.wait_for_network_ready
  ]
}

resource "azurerm_user_assigned_identity" "github_runner" {
  name                = var.github_runner_identity_name
  location            = azurerm_resource_group.private_rg.location
  resource_group_name = azurerm_resource_group.private_rg.name
  tags                = local.tags
}
