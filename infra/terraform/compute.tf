#################################################################################
# Compute Services
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

# Query the AI Services account (Foundry resource) to derive endpoint URLs.
# Uses modern resource + project pattern (not deprecated "hub" pattern).
data "azapi_resource" "ai_services_account" {
  type                   = "Microsoft.CognitiveServices/accounts@2024-10-01"
  resource_id            = module.ai_foundry.ai_foundry_id
  response_export_values = ["properties.endpoint"]
}

locals {
  # AI Services account endpoint (Cognitive Services domain)
  ai_services_account_endpoint = data.azapi_resource.ai_services_account.output.properties.endpoint

  # Convert to modern AI services domain for project/agents API access
  ai_services_api_endpoint = replace(local.ai_services_account_endpoint, ".cognitiveservices.azure.com", ".services.ai.azure.com")

  # Project workspace name within the AI Services account
  ai_project_name = module.ai_foundry.ai_foundry_project_name["cadence"]

  # Full project endpoint for Azure AI Foundry agents and client SDKs
  ai_project_endpoint = "${trimsuffix(local.ai_services_api_endpoint, "/")}/api/projects/${local.ai_project_name}"
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

resource "azurerm_static_web_app" "frontend" {
  name                = "${var.name_prefix}-${local.identifier}-web"
  resource_group_name = azurerm_resource_group.private_rg.name
  location            = azurerm_resource_group.private_rg.location
  sku_tier            = "Free"
  sku_size            = "Free"
  tags                = local.tags
}
