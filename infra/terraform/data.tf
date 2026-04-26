#################################################################################
# Data Services
#################################################################################

module "ai_storage" {
  source                        = "Azure/avm-res-storage-storageaccount/azurerm"
  name                          = replace("${local.identifier}foundry", "-", "")
  resource_group_name           = azurerm_resource_group.private_rg.name
  location                      = var.region_aifoundry
  account_tier                  = "Standard"
  account_replication_type      = "LRS"
  public_network_access_enabled = false
  shared_access_key_enabled     = false
  tags                          = local.tags

  containers = {
    nl2sql = {
      name                  = "nl2sql"
      container_access_type = "private"
    }
  }

  private_endpoints = {
    blob = {
      subnet_resource_id            = azurerm_subnet.private_endpoints.id
      subresource_name              = "blob"
      private_dns_zone_resource_ids = [azurerm_private_dns_zone.this["privatelink.blob.core.windows.net"].id]
    }
  }

  role_assignments = {
    storage_blob_contributor = {
      role_definition_id_or_name = "Storage Blob Data Contributor"
      principal_id               = data.azurerm_client_config.current.object_id
    }
  }

  depends_on = [time_sleep.wait_for_network_ready]
}

resource "time_sleep" "wait_for_storage_rbac" {
  depends_on      = [module.ai_storage]
  create_duration = "60s"
}

resource "azurerm_storage_blob" "nl2sql_tables" {
  for_each               = var.enable_local_exec_provisioning ? fileset("${path.module}/../data/tables", "**/*.json") : toset([])
  name                   = "tables/${each.value}"
  storage_account_name   = module.ai_storage.name
  storage_container_name = "nl2sql"
  type                   = "Block"
  source                 = "${path.module}/../data/tables/${each.value}"
  content_type           = "application/json"

  depends_on = [time_sleep.wait_for_storage_rbac]
}

resource "azurerm_storage_blob" "nl2sql_query_templates" {
  for_each               = var.enable_local_exec_provisioning ? fileset("${path.module}/../data/query_templates", "*.json") : toset([])
  name                   = "query_templates/${each.value}"
  storage_account_name   = module.ai_storage.name
  storage_container_name = "nl2sql"
  type                   = "Block"
  source                 = "${path.module}/../data/query_templates/${each.value}"
  content_type           = "application/json"

  depends_on = [time_sleep.wait_for_storage_rbac]
}

module "ai_cosmosdb" {
  source                        = "Azure/avm-res-documentdb-databaseaccount/azurerm"
  name                          = "${local.identifier}-foundry"
  resource_group_name           = azurerm_resource_group.private_rg.name
  location                      = var.region_aifoundry
  public_network_access_enabled = false
  analytical_storage_enabled    = true
  automatic_failover_enabled    = true

  geo_locations = [
    {
      location          = var.region_aifoundry
      failover_priority = 0
      zone_redundant    = false
    }
  ]

  private_endpoints = {
    cosmosdb = {
      subnet_resource_id            = azurerm_subnet.private_endpoints.id
      subresource_name              = "SQL"
      private_dns_zone_resource_ids = [azurerm_private_dns_zone.this["privatelink.documents.azure.com"].id]
    }
  }

  diagnostic_settings = {
    to_law = {
      name                  = "to-law"
      workspace_resource_id = module.log_analytics.resource_id
      metric_categories     = ["SLI", "Requests"]
    }
  }

  tags = local.tags
}

resource "azurerm_cosmosdb_sql_role_assignment" "current_user" {
  resource_group_name = azurerm_resource_group.private_rg.name
  account_name        = module.ai_cosmosdb.name
  role_definition_id  = "${module.ai_cosmosdb.resource_id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002"
  principal_id        = data.azurerm_client_config.current.object_id
  scope               = module.ai_cosmosdb.resource_id
}

module "ai_search" {
  source                        = "Azure/avm-res-search-searchservice/azurerm"
  name                          = local.identifier
  resource_group_name           = azurerm_resource_group.private_rg.name
  location                      = var.region_search
  sku                           = "basic"
  public_network_access_enabled = true
  network_rule_bypass_option    = "AzureServices"
  allowed_ips                   = [local.deployer_public_ip_cidr]
  local_authentication_enabled  = true
  authentication_failure_mode   = "http401WithBearerChallenge"
  tags                          = local.tags

  managed_identities = {
    system_assigned = true
  }

  private_endpoints = {
    search = {
      name                            = "pe-${local.identifier}-search"
      private_service_connection_name = "psc-${local.identifier}-search"
      location                        = azurerm_resource_group.private_rg.location
      subnet_resource_id              = azurerm_subnet.private_endpoints.id
      private_dns_zone_resource_ids   = [azurerm_private_dns_zone.this["privatelink.search.windows.net"].id]
    }
  }

  role_assignments = {
    search_service_contributor = {
      role_definition_id_or_name = "Search Service Contributor"
      principal_id               = data.azurerm_client_config.current.object_id
    }
    search_index_data_reader = {
      role_definition_id_or_name = "Search Index Data Reader"
      principal_id               = data.azurerm_client_config.current.object_id
    }
  }

  diagnostic_settings = {
    to_law = {
      name                  = "to-law"
      workspace_resource_id = module.log_analytics.resource_id
    }
  }

  depends_on = [time_sleep.wait_for_network_ready]
}

module "sql_server" {
  source              = "Azure/avm-res-sql-server/azurerm"
  name                = "${local.identifier}-sql"
  resource_group_name = azurerm_resource_group.private_rg.name
  location            = azurerm_resource_group.private_rg.location
  server_version      = "12.0"
  tags                = local.tags

  azuread_administrator = {
    azuread_authentication_only = true
    login_username              = local.sql_admin_login_username
    object_id                   = local.sql_admin_object_id
    tenant_id                   = data.azurerm_client_config.current.tenant_id
  }

  managed_identities = {
    system_assigned = true
  }

  databases = {
    wideworldimporters = {
      name        = var.sql_database_name
      sku_name    = "S0"
      max_size_gb = 250
    }
  }

  public_network_access_enabled = false

  private_endpoints = {
    sql = {
      subnet_resource_id            = azurerm_subnet.private_endpoints.id
      subresource_name              = "sqlServer"
      private_dns_zone_resource_ids = [azurerm_private_dns_zone.this["privatelink.database.windows.net"].id]
    }
  }

  depends_on = [time_sleep.wait_for_network_ready]
}

resource "null_resource" "import_wideworldimporters" {
  count      = var.enable_local_exec_provisioning ? 1 : 0
  depends_on = [module.sql_server]

  triggers = {
    sql_server_name = module.sql_server.resource.name
    database_name   = var.sql_database_name
  }

  provisioner "local-exec" {
    interpreter = ["pwsh", "-Command"]
    command     = "& '${path.module}/../scripts/import-wideworldimporters.ps1' -SqlServerName '${module.sql_server.resource.name}' -DatabaseName '${var.sql_database_name}' -ResourceGroup '${azurerm_resource_group.private_rg.name}' -Force"
  }
}


#################################################################################
# Search configuration for private deployments
#################################################################################

# AI Search data-plane setup is performed by the one-time private runner workflow:
# .github/workflows/provision-private-data.yml
# which calls infra/scripts/configure-ai-search.sh from inside infra/terraform.