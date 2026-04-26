# networking.tf - Private networking foundation for Cadence

data "azurerm_client_config" "current" {}

data "azurerm_subscription" "current" {}

resource "random_string" "naming" {
  special = false
  upper   = false
  length  = 5
}

resource "random_string" "alpha_prefix" {
  special = false
  upper   = false
  length  = 1
  lower   = true
  numeric = false
}

locals {
  identifier = "${random_string.alpha_prefix.result}${random_string.naming.result}"

  rg_name = coalesce(var.resource_group_name, "${var.name_prefix}-${local.identifier}")

  tags = merge(
    {
      Environment = "PrivateNetworking"
      ManagedBy   = "Terraform"
      OwnerObject = data.azurerm_client_config.current.object_id
      Workload    = "cadence"
    },
    var.tags
  )
}

resource "azurerm_resource_group" "private_rg" {
  name     = local.rg_name
  location = var.region
  tags     = local.tags
}

resource "azurerm_virtual_network" "private_vnet" {
  name                = "${var.name_prefix}-${local.identifier}-vnet"
  location            = azurerm_resource_group.private_rg.location
  resource_group_name = azurerm_resource_group.private_rg.name
  address_space       = var.vnet_address_space
  tags                = local.tags
}

resource "azurerm_subnet" "private_endpoints" {
  name                 = "private-endpoints"
  resource_group_name  = azurerm_resource_group.private_rg.name
  virtual_network_name = azurerm_virtual_network.private_vnet.name
  address_prefixes     = [var.private_endpoints_subnet_cidr]

  private_endpoint_network_policies = "Disabled"
}

resource "azurerm_subnet" "application" {
  name                 = "application"
  resource_group_name  = azurerm_resource_group.private_rg.name
  virtual_network_name = azurerm_virtual_network.private_vnet.name
  address_prefixes     = [var.application_subnet_cidr]
}

resource "azurerm_subnet" "container_apps" {
  name                 = "container-apps"
  resource_group_name  = azurerm_resource_group.private_rg.name
  virtual_network_name = azurerm_virtual_network.private_vnet.name
  address_prefixes     = [var.container_apps_subnet_cidr]

  delegation {
    name = "Microsoft.App.environments"

    service_delegation {
      name    = "Microsoft.App/environments"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

resource "azurerm_subnet" "ai_agent_services" {
  name                 = "ai-agent-services"
  resource_group_name  = azurerm_resource_group.private_rg.name
  virtual_network_name = azurerm_virtual_network.private_vnet.name
  address_prefixes     = [var.ai_agent_services_subnet_cidr]

  delegation {
    name = "Microsoft.App.environments"

    service_delegation {
      name    = "Microsoft.App/environments"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

resource "azurerm_subnet" "utility" {
  name                 = "utility"
  resource_group_name  = azurerm_resource_group.private_rg.name
  virtual_network_name = azurerm_virtual_network.private_vnet.name
  address_prefixes     = [var.utility_subnet_cidr]
}

resource "azurerm_subnet" "azure_bastion" {
  name                 = "AzureBastionSubnet"
  resource_group_name  = azurerm_resource_group.private_rg.name
  virtual_network_name = azurerm_virtual_network.private_vnet.name
  address_prefixes     = [var.bastion_subnet_cidr]
}

resource "azurerm_subnet" "data" {
  name                 = "data"
  resource_group_name  = azurerm_resource_group.private_rg.name
  virtual_network_name = azurerm_virtual_network.private_vnet.name
  address_prefixes     = [var.data_subnet_cidr]
}

resource "time_sleep" "wait_for_network_ready" {
  depends_on = [
    azurerm_virtual_network.private_vnet,
    azurerm_subnet.private_endpoints,
    azurerm_subnet.application,
    azurerm_subnet.container_apps,
    azurerm_subnet.ai_agent_services,
    azurerm_subnet.utility,
    azurerm_subnet.azure_bastion,
    azurerm_subnet.data,
  ]
  create_duration = "30s"
}

resource "azurerm_network_security_group" "application" {
  name                = "${var.name_prefix}-${local.identifier}-app-nsg"
  location            = azurerm_resource_group.private_rg.location
  resource_group_name = azurerm_resource_group.private_rg.name
  tags                = local.tags
}

resource "azurerm_network_security_group" "data" {
  name                = "${var.name_prefix}-${local.identifier}-data-nsg"
  location            = azurerm_resource_group.private_rg.location
  resource_group_name = azurerm_resource_group.private_rg.name
  tags                = local.tags
}

resource "azurerm_subnet_network_security_group_association" "application" {
  subnet_id                 = azurerm_subnet.application.id
  network_security_group_id = azurerm_network_security_group.application.id
}

resource "azurerm_subnet_network_security_group_association" "data" {
  subnet_id                 = azurerm_subnet.data.id
  network_security_group_id = azurerm_network_security_group.data.id
}

resource "azurerm_private_dns_zone" "this" {
  for_each = var.private_dns_zone_names

  name                = each.key
  resource_group_name = azurerm_resource_group.private_rg.name
  tags                = local.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "this" {
  for_each = var.private_dns_zone_names

  name                  = "${replace(each.key, ".", "-")}-link"
  resource_group_name   = azurerm_resource_group.private_rg.name
  private_dns_zone_name = azurerm_private_dns_zone.this[each.key].name
  virtual_network_id    = azurerm_virtual_network.private_vnet.id
}

resource "azurerm_private_endpoint" "this" {
  for_each = var.private_endpoints

  name                = "${var.name_prefix}-${local.identifier}-${each.key}-pe"
  location            = azurerm_resource_group.private_rg.location
  resource_group_name = azurerm_resource_group.private_rg.name
  subnet_id           = azurerm_subnet.private_endpoints.id
  tags                = local.tags

  private_service_connection {
    name                           = "${each.key}-psc"
    private_connection_resource_id = each.value.resource_id
    subresource_names              = each.value.subresource_names
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "${each.key}-dns-zone-group"
    private_dns_zone_ids = [azurerm_private_dns_zone.this[each.value.dns_zone_name].id]
  }

  lifecycle {
    precondition {
      condition     = contains(keys(azurerm_private_dns_zone.this), each.value.dns_zone_name)
      error_message = "private_endpoints[*].dns_zone_name must exist in var.private_dns_zone_names."
    }
  }
}

resource "azurerm_public_ip" "bastion" {
  name                = "${local.identifier}-bastion-pip"
  resource_group_name = azurerm_resource_group.private_rg.name
  location            = azurerm_resource_group.private_rg.location
  allocation_method   = "Static"
  sku                 = "Standard"
  tags                = local.tags
}

resource "azurerm_bastion_host" "main" {
  name                = "${local.identifier}-bastion"
  resource_group_name = azurerm_resource_group.private_rg.name
  location            = azurerm_resource_group.private_rg.location
  sku                 = "Basic"
  copy_paste_enabled  = true
  tags                = local.tags

  ip_configuration {
    name                 = "bastion-ip-config"
    subnet_id            = azurerm_subnet.azure_bastion.id
    public_ip_address_id = azurerm_public_ip.bastion.id
  }

  depends_on = [time_sleep.wait_for_network_ready]
}
