#################################################################################
# Observability Services
#################################################################################

locals {
  github_federated_principal_object_id = var.github_federated_principal_object_id == null ? "" : trimspace(var.github_federated_principal_object_id)
  github_federated_principal_client_id = var.github_federated_principal_client_id == null ? "" : trimspace(var.github_federated_principal_client_id)
  azure_ad_allowed_tenant_ids = distinct(compact(concat(
    var.azure_ad_allowed_tenant_ids,
    [data.azurerm_client_config.current.tenant_id]
  )))

  # RBAC requires a service principal object ID. Prefer resolving from client ID when provided.
  github_federated_rbac_principal_object_id = local.github_federated_principal_client_id != "" ? data.azuread_service_principal.github_federated[0].object_id : local.github_federated_principal_object_id

  sql_admin_object_id = trimspace(
    coalesce(
      var.sql_azuread_admin_object_id,
      var.github_federated_principal_object_id,
      data.azurerm_client_config.current.object_id
    )
  )

  sql_admin_login_username = trimspace(
    coalesce(
      var.sql_azuread_admin_login_username,
      var.github_federated_principal_client_id,
      data.azurerm_client_config.current.client_id
    )
  )

  deployer_public_ip_cidr = "${trimspace(data.http.deployer_public_ip.response_body)}/32"

  utility_vm_setup_script = templatefile("${path.module}/../scripts/util_vm_setup_choco.ps1", {})
}

data "http" "deployer_public_ip" {
  url = "https://api.ipify.org"
}

data "azuread_service_principal" "github_federated" {
  count = local.github_federated_principal_client_id != "" ? 1 : 0

  client_id = local.github_federated_principal_client_id
}

module "log_analytics" {
  source                                             = "Azure/avm-res-operationalinsights-workspace/azurerm"
  name                                               = "${local.identifier}-law"
  resource_group_name                                = azurerm_resource_group.private_rg.name
  location                                           = azurerm_resource_group.private_rg.location
  log_analytics_workspace_internet_ingestion_enabled = true
  log_analytics_workspace_internet_query_enabled     = true
  tags                                               = local.tags
}

module "application_insights" {
  source              = "Azure/avm-res-insights-component/azurerm"
  name                = "${local.identifier}-appi"
  resource_group_name = azurerm_resource_group.private_rg.name
  location            = azurerm_resource_group.private_rg.location
  workspace_id        = module.log_analytics.resource_id
  application_type    = "web"
  tags                = local.tags
}


#################################################################################
# Utility VM + Bastion
#################################################################################

resource "azurerm_network_interface" "utility_vm" {
  name                = "${local.identifier}-util-nic"
  location            = azurerm_resource_group.private_rg.location
  resource_group_name = azurerm_resource_group.private_rg.name
  tags                = local.tags

  ip_configuration {
    name                          = "internal"
    subnet_id                     = azurerm_subnet.utility.id
    private_ip_address_allocation = "Dynamic"
  }
}

resource "azurerm_windows_virtual_machine" "utility" {
  name                = "${local.identifier}-util"
  computer_name       = "${local.identifier}-util"
  resource_group_name = azurerm_resource_group.private_rg.name
  location            = azurerm_resource_group.private_rg.location
  size                = var.utility_vm_size
  admin_username      = "azureuser"
  admin_password      = var.utility_vm_admin_password
  tags                = local.tags

  network_interface_ids = [
    azurerm_network_interface.utility_vm.id,
  ]

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "Premium_LRS"
  }

  source_image_reference {
    publisher = "MicrosoftWindowsDesktop"
    offer     = "windows-11"
    sku       = "win11-25h2-pro"
    version   = "latest"
  }

  lifecycle {
    # Azure Policy may attach a system-assigned identity after creation.
    ignore_changes = [identity]
  }

  depends_on = [time_sleep.wait_for_network_ready]
}

resource "azurerm_virtual_machine_extension" "utility_setup_script" {
  name                       = "setup-script"
  virtual_machine_id         = azurerm_windows_virtual_machine.utility.id
  publisher                  = "Microsoft.Compute"
  type                       = "CustomScriptExtension"
  type_handler_version       = "1.10"
  auto_upgrade_minor_version = true
  tags                       = local.tags

  protected_settings = jsonencode({
    commandToExecute = "powershell -ExecutionPolicy Unrestricted -Command \"$script = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('${base64encode(local.utility_vm_setup_script)}')); Invoke-Expression $script\""
  })

  depends_on = [azurerm_windows_virtual_machine.utility]
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


#################################################################################
# Container Registry (private)
#################################################################################

module "container_registry" {
  source                        = "Azure/avm-res-containerregistry-registry/azurerm"
  name                          = replace("${local.identifier}acr", "-", "")
  resource_group_name           = azurerm_resource_group.private_rg.name
  location                      = azurerm_resource_group.private_rg.location
  sku                           = "Premium"
  zone_redundancy_enabled       = false
  public_network_access_enabled = true
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


#################################################################################
# Storage Account for Microsoft Foundry blob uploads and NL2SQL data (private)
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


#################################################################################
# Cosmos DB Account for Microsoft Foundry agent service thread storage (private)
#################################################################################

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


#################################################################################
# AI Search - linked to Microsoft Foundry (private)
#################################################################################

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

resource "azurerm_role_assignment" "ai_search_storage_reader" {
  scope                = module.ai_storage.resource_id
  role_definition_name = "Storage Blob Data Reader"
  principal_id         = module.ai_search.resource.identity[0].principal_id
}

resource "azurerm_search_shared_private_link_service" "ai_search_storage_blob" {
  name               = "storage-blob"
  search_service_id  = module.ai_search.resource_id
  target_resource_id = module.ai_storage.resource_id
  subresource_name   = "blob"
  request_message    = "Allow AI Search indexers to read NL2SQL blobs over private link."

  depends_on = [
    module.ai_search,
    module.ai_storage,
    azurerm_role_assignment.ai_search_storage_reader,
  ]
}

resource "azurerm_role_assignment" "ai_search_openai_user" {
  scope                = module.ai_foundry.ai_foundry_id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = module.ai_search.resource.identity[0].principal_id
}


#################################################################################
# AI Foundry (Standard Private + Agent subnet injection)
#################################################################################

module "ai_foundry" {
  source  = "Azure/avm-ptn-aiml-ai-foundry/azurerm"
  version = "~> 0.8.0"

  base_name                  = local.identifier
  location                   = var.region_aifoundry
  resource_group_resource_id = azurerm_resource_group.private_rg.id

  tags = local.tags

  create_byor                         = false
  create_private_endpoints            = true
  private_endpoint_subnet_resource_id = azurerm_subnet.private_endpoints.id

  ai_foundry = {
    create_ai_agent_service = true
    private_dns_zone_resource_ids = [
      azurerm_private_dns_zone.this["privatelink.openai.azure.com"].id,
      azurerm_private_dns_zone.this["privatelink.cognitiveservices.azure.com"].id,
      azurerm_private_dns_zone.this["privatelink.services.ai.azure.com"].id
    ]
    network_injections = [{
      scenario                   = "agent"
      subnetArmId                = azurerm_subnet.ai_agent_services.id
      useMicrosoftManagedNetwork = false
    }]
  }

  ai_projects = {
    cadence = {
      name                       = "cadence"
      display_name               = "Cadence"
      description                = "Cadence agents and related resources"
      create_project_connections = true
      cosmos_db_connection = {
        existing_resource_id = module.ai_cosmosdb.resource_id
      }
      storage_account_connection = {
        existing_resource_id = module.ai_storage.resource_id
      }
      ai_search_connection = {
        existing_resource_id = module.ai_search.resource_id
      }
    }
  }

  cosmosdb_definition = {
    byor = {
      existing_resource_id = module.ai_cosmosdb.resource_id
    }
  }

  storage_account_definition = {
    byor = {
      existing_resource_id = module.ai_storage.resource_id
    }
  }

  ai_search_definition = {
    byor = {
      existing_resource_id       = module.ai_search.resource_id
      enable_diagnostic_settings = false
    }
  }

  depends_on = [
    module.ai_storage,
    module.ai_cosmosdb,
    module.ai_search
  ]
}

# Keep Foundry private endpoints while allowing public access for portal/tooling.
resource "azapi_update_resource" "ai_foundry_public_network_access" {
  type        = "Microsoft.CognitiveServices/accounts@2025-10-01-preview"
  resource_id = module.ai_foundry.ai_foundry_id
  body = {
    properties = {
      publicNetworkAccess = "Enabled"
    }
  }

  depends_on = [module.ai_foundry]
}

resource "azurerm_cosmosdb_sql_role_assignment" "foundry_project" {
  resource_group_name = azurerm_resource_group.private_rg.name
  account_name        = module.ai_cosmosdb.name
  role_definition_id  = "${module.ai_cosmosdb.resource_id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002"
  principal_id        = module.ai_foundry.ai_foundry_project_system_identity_principal_id["cadence"]
  scope               = module.ai_cosmosdb.resource_id
}


#################################################################################
# AI Model Deployments
#################################################################################

resource "azapi_resource" "ai_model_deployment_gpt5" {
  name      = "gpt-5-chat"
  parent_id = module.ai_foundry.ai_foundry_id
  type      = "Microsoft.CognitiveServices/accounts/deployments@2025-10-01-preview"
  body = {
    properties = {
      model = {
        format  = "OpenAI"
        name    = "gpt-5-chat"
        version = "2025-10-03"
      }
      versionUpgradeOption = "OnceNewDefaultVersionAvailable"
    }
    sku = {
      name     = "GlobalStandard"
      capacity = 150
    }
  }
  schema_validation_enabled = false

  depends_on = [module.ai_foundry]
}

resource "azapi_resource" "ai_model_deployment_gpt52" {
  name      = "gpt-5.2-chat"
  parent_id = module.ai_foundry.ai_foundry_id
  type      = "Microsoft.CognitiveServices/accounts/deployments@2025-10-01-preview"
  body = {
    properties = {
      model = {
        format  = "OpenAI"
        name    = "gpt-5.2-chat"
        version = "2025-12-11"
      }
      versionUpgradeOption = "OnceNewDefaultVersionAvailable"
    }
    sku = {
      name     = "GlobalStandard"
      capacity = 150
    }
  }
  schema_validation_enabled = false

  depends_on = [azapi_resource.ai_model_deployment_gpt5]
}

resource "azapi_resource" "ai_model_deployment_embedding_small" {
  name      = "embedding-small"
  parent_id = module.ai_foundry.ai_foundry_id
  type      = "Microsoft.CognitiveServices/accounts/deployments@2025-10-01-preview"
  body = {
    properties = {
      model = {
        format  = "OpenAI"
        name    = "text-embedding-3-small"
        version = "1"
      }
      versionUpgradeOption = "OnceNewDefaultVersionAvailable"
    }
    sku = {
      name     = "GlobalStandard"
      capacity = 150
    }
  }
  schema_validation_enabled = false

  depends_on = [azapi_resource.ai_model_deployment_gpt52]
}

resource "azapi_resource" "ai_model_deployment_embedding_large" {
  name      = "embedding-large"
  parent_id = module.ai_foundry.ai_foundry_id
  type      = "Microsoft.CognitiveServices/accounts/deployments@2025-10-01-preview"
  body = {
    properties = {
      model = {
        format  = "OpenAI"
        name    = "text-embedding-3-large"
        version = "1"
      }
      versionUpgradeOption = "OnceNewDefaultVersionAvailable"
    }
    sku = {
      name     = "GlobalStandard"
      capacity = 120
    }
  }
  schema_validation_enabled = false

  depends_on = [azapi_resource.ai_model_deployment_embedding_small]
}

resource "azapi_resource" "ai_model_deployment_gpt41" {
  name      = "gpt-4.1"
  parent_id = module.ai_foundry.ai_foundry_id
  type      = "Microsoft.CognitiveServices/accounts/deployments@2025-10-01-preview"
  body = {
    properties = {
      model = {
        format  = "OpenAI"
        name    = "gpt-4.1"
        version = "2025-04-14"
      }
      versionUpgradeOption = "OnceNewDefaultVersionAvailable"
    }
    sku = {
      name     = "GlobalStandard"
      capacity = 150
    }
  }
  schema_validation_enabled = false

  depends_on = [azapi_resource.ai_model_deployment_embedding_large]
}

resource "azapi_resource" "ai_model_deployment_gpt41_mini" {
  name      = "gpt-4.1-mini"
  parent_id = module.ai_foundry.ai_foundry_id
  type      = "Microsoft.CognitiveServices/accounts/deployments@2025-10-01-preview"
  body = {
    properties = {
      model = {
        format  = "OpenAI"
        name    = "gpt-4.1-mini"
        version = "2025-04-14"
      }
      versionUpgradeOption = "OnceNewDefaultVersionAvailable"
    }
    sku = {
      name     = "GlobalStandard"
      capacity = 150
    }
  }
  schema_validation_enabled = false

  depends_on = [azapi_resource.ai_model_deployment_gpt41]
}


#################################################################################
# Azure SQL Database (private endpoint + no public access)
#################################################################################

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

resource "null_resource" "sql_server_directory_readers" {
  count = var.enable_sql_server_directory_readers_grant ? 1 : 0

  triggers = {
    sql_server_name = module.sql_server.resource.name
    sql_principal   = coalesce(try(module.sql_server.resource.identity[0].principal_id, ""), "")
  }

  provisioner "local-exec" {
    interpreter = ["bash", "-lc"]
    command     = "${path.module}/../scripts/ensure-sql-directory-readers.sh '${self.triggers.sql_principal}'"
  }

  depends_on = [module.sql_server]
}

resource "azurerm_role_assignment" "github_federated_storage_blob_contributor" {
  count = local.github_federated_rbac_principal_object_id != "" ? 1 : 0

  scope                = module.ai_storage.resource_id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = local.github_federated_rbac_principal_object_id
}

resource "azurerm_role_assignment" "github_federated_search_contributor" {
  count = local.github_federated_rbac_principal_object_id != "" ? 1 : 0

  scope                = module.ai_search.resource_id
  role_definition_name = "Search Service Contributor"
  principal_id         = local.github_federated_rbac_principal_object_id
}

resource "azurerm_role_assignment" "github_federated_sql_db_contributor" {
  count = local.github_federated_rbac_principal_object_id != "" ? 1 : 0

  scope                = module.sql_server.resource_id
  role_definition_name = "SQL DB Contributor"
  principal_id         = local.github_federated_rbac_principal_object_id
}

resource "azurerm_role_assignment" "github_federated_acr_push" {
  count = local.github_federated_rbac_principal_object_id != "" ? 1 : 0

  scope                = module.container_registry.resource_id
  role_definition_name = "AcrPush"
  principal_id         = local.github_federated_rbac_principal_object_id
}

resource "azurerm_role_assignment" "github_federated_rg_contributor" {
  count = local.github_federated_rbac_principal_object_id != "" ? 1 : 0

  scope                = azurerm_resource_group.private_rg.id
  role_definition_name = "Contributor"
  principal_id         = local.github_federated_rbac_principal_object_id
}

resource "azurerm_role_assignment" "github_federated_rg_user_access_admin" {
  count = local.github_federated_rbac_principal_object_id != "" ? 1 : 0

  scope                = azurerm_resource_group.private_rg.id
  role_definition_name = "User Access Administrator"
  principal_id         = local.github_federated_rbac_principal_object_id
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
# which calls infra/scripts/configure-ai-search.sh from inside private networking.


#################################################################################
# Container App Environment (VNet injected + scalable workload profile)
#################################################################################

module "container_app_environment" {
  source  = "Azure/avm-res-app-managedenvironment/azurerm"
  version = "~> 0.4"

  name                = "${local.identifier}-cae"
  resource_group_name = azurerm_resource_group.private_rg.name
  location            = azurerm_resource_group.private_rg.location

  log_analytics_workspace = {
    resource_id = module.log_analytics.resource_id
  }

  infrastructure_subnet_id       = azurerm_subnet.container_apps.id
  internal_load_balancer_enabled = false
  public_network_access_enabled  = true

  workload_profile = [
    {
      name                  = "Dedicated"
      workload_profile_type = "D4"
      minimum_count         = 1
      maximum_count         = 3
    }
  ]

  zone_redundancy_enabled = false
  tags                    = local.tags
}


#################################################################################
# Container App for Backend API
#################################################################################

data "azapi_resource" "ai_foundry_hub" {
  type                   = "Microsoft.CognitiveServices/accounts@2024-10-01"
  resource_id            = module.ai_foundry.ai_foundry_id
  response_export_values = ["properties.endpoint"]
}

locals {
  ai_hub_endpoint     = data.azapi_resource.ai_foundry_hub.output.properties.endpoint
  ai_project_name     = module.ai_foundry.ai_foundry_project_name["cadence"]
  ai_project_endpoint = "${trimsuffix(local.ai_hub_endpoint, "/")}/api/projects/${local.ai_project_name}"
}

resource "azurerm_user_assigned_identity" "api_identity" {
  name                = "${local.identifier}-api-identity"
  resource_group_name = azurerm_resource_group.private_rg.name
  location            = azurerm_resource_group.private_rg.location
  tags                = local.tags
}

resource "azurerm_role_assignment" "api_acr_pull" {
  scope                = module.container_registry.resource_id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.api_identity.principal_id
}

resource "azurerm_role_assignment" "api_ai_foundry_developer_containerapp" {
  scope                = module.ai_foundry.ai_foundry_id
  role_definition_name = "Azure AI Developer"
  principal_id         = azurerm_user_assigned_identity.api_identity.principal_id
}

resource "azurerm_role_assignment" "api_cognitive_services_user" {
  scope                = module.ai_foundry.ai_foundry_id
  role_definition_name = "Cognitive Services User"
  principal_id         = azurerm_user_assigned_identity.api_identity.principal_id
}

resource "azurerm_role_assignment" "api_ai_foundry_project" {
  scope                = module.ai_foundry.ai_foundry_project_id["cadence"]
  role_definition_name = "Azure AI Developer"
  principal_id         = azurerm_user_assigned_identity.api_identity.principal_id
}

resource "azurerm_role_assignment" "api_search" {
  scope                = module.ai_search.resource_id
  role_definition_name = "Search Index Data Reader"
  principal_id         = azurerm_user_assigned_identity.api_identity.principal_id
}

resource "azurerm_role_assignment" "api_sql" {
  scope                = module.sql_server.resource_id
  role_definition_name = "Contributor"
  principal_id         = azurerm_user_assigned_identity.api_identity.principal_id
}

resource "azurerm_role_assignment" "api_storage" {
  scope                = module.ai_storage.resource_id
  role_definition_name = "Storage Blob Data Reader"
  principal_id         = azurerm_user_assigned_identity.api_identity.principal_id
}

resource "azurerm_role_assignment" "api_cosmos" {
  scope                = module.ai_cosmosdb.resource_id
  role_definition_name = "Cosmos DB Account Reader Role"
  principal_id         = azurerm_user_assigned_identity.api_identity.principal_id
}

resource "azurerm_container_app" "api" {
  name                         = "${local.identifier}-api"
  resource_group_name          = azurerm_resource_group.private_rg.name
  container_app_environment_id = module.container_app_environment.resource_id
  revision_mode                = "Single"
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.api_identity.id]
  }

  registry {
    server   = module.container_registry.resource.login_server
    identity = azurerm_user_assigned_identity.api_identity.id
  }

  ingress {
    external_enabled = true
    target_port      = 8000
    transport        = "http"

    cors {
      allowed_origins = [
        "https://${azurerm_static_web_app.frontend.default_host_name}",
        "http://localhost:3000",
        "http://127.0.0.1:3000"
      ]
      allowed_methods = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]
      allowed_headers = ["*"]
      exposed_headers = ["*"]
    }

    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }

  template {
    min_replicas = 1
    max_replicas = 3

    container {
      name   = "api"
      image  = "mcr.microsoft.com/k8se/quickstart:latest"
      cpu    = 1.0
      memory = "2Gi"

      env {
        name  = "AZURE_CLIENT_ID"
        value = azurerm_user_assigned_identity.api_identity.client_id
      }
      env {
        name  = "AZURE_AD_TENANT_ID"
        value = local.azure_ad_allowed_tenant_ids[0]
      }
      env {
        name  = "AZURE_AD_TENANT_IDS"
        value = join(",", local.azure_ad_allowed_tenant_ids)
      }
      env {
        name  = "AZURE_AD_CLIENT_ID"
        value = var.frontend_app_client_id
      }
      env {
        name  = "CORS_ALLOWED_ORIGINS"
        value = "https://${azurerm_static_web_app.frontend.default_host_name}"
      }
      env {
        name  = "AZURE_AI_PROJECT_ENDPOINT"
        value = local.ai_project_endpoint
      }
      env {
        name  = "AZURE_AI_MODEL_DEPLOYMENT_NAME"
        value = "gpt-5-chat"
      }
      env {
        name  = "AZURE_AI_EMBEDDING_DEPLOYMENT"
        value = "embedding-large"
      }
      env {
        name  = "AZURE_SEARCH_ENDPOINT"
        value = "https://${module.ai_search.resource.name}.search.windows.net"
      }
      env {
        name  = "AZURE_SEARCH_INDEX_TABLES"
        value = "tables"
      }
      env {
        name  = "AZURE_SEARCH_INDEX_QUERY_TEMPLATES"
        value = "query_templates"
      }
      env {
        name  = "AZURE_SQL_SERVER"
        value = module.sql_server.resource.fully_qualified_domain_name
      }
      env {
        name  = "AZURE_SQL_DATABASE"
        value = var.sql_database_name
      }
      env {
        name  = "APPLICATIONINSIGHTS_CONNECTION_STRING"
        value = module.application_insights.connection_string
      }
      env {
        name  = "ENABLE_INSTRUMENTATION"
        value = "true"
      }
      env {
        name  = "ENABLE_SENSITIVE_DATA"
        value = "true"
      }
      env {
        name  = "QUERY_TEMPLATE_CONFIDENCE_THRESHOLD"
        value = "0.80"
      }
      env {
        name  = "QUERY_TEMPLATE_AMBIGUITY_GAP"
        value = "0.05"
      }
      env {
        name  = "AZURE_AI_CHAT_MODEL"
        value = "gpt-4.1"
      }
      env {
        name  = "AZURE_AI_NL2SQL_MODEL"
        value = "gpt-4.1-mini"
      }
      env {
        name  = "AZURE_AI_PARAM_EXTRACTOR_MODEL"
        value = "gpt-4.1-mini"
      }
    }
  }

  lifecycle {
    ignore_changes = [
      template[0].container[0].image
    ]
  }

  depends_on = [
    azurerm_role_assignment.api_acr_pull,
    azurerm_role_assignment.api_ai_foundry_developer_containerapp,
    azurerm_role_assignment.api_search,
    azurerm_role_assignment.api_storage
  ]
}

#################################################################################
# Frontend Hosting - Azure Static Web Apps
#################################################################################

resource "azurerm_static_web_app" "frontend" {
  name                = "${var.name_prefix}-${local.identifier}-web"
  resource_group_name = azurerm_resource_group.private_rg.name
  location            = azurerm_resource_group.private_rg.location
  sku_tier            = "Free"
  sku_size            = "Free"
  tags                = local.tags
}
