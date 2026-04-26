#################################################################################
# AI Platform Services
#################################################################################

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
