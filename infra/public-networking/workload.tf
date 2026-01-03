#################################################################################
# Deployer IP Address
#################################################################################

# Get the public IP of the machine running Terraform
data "http" "deployer_ip" {
  url = "https://api.ipify.org"
}

locals {
  deployer_ip = chomp(data.http.deployer_ip.response_body)
}


#################################################################################
# Observability Services
#################################################################################

# Log Analytics Workspace (shared)
module "log_analytics" {
  source  = "Azure/avm-res-operationalinsights-workspace/azurerm"
  name                = "${local.identifier}-law"
  resource_group_name = azurerm_resource_group.shared_rg.name
  location            = azurerm_resource_group.shared_rg.location
  tags                = local.tags
}

# Application Insights
module "application_insights" {
  source  = "Azure/avm-res-insights-component/azurerm"
  name                = "${local.identifier}-appi"
  resource_group_name = azurerm_resource_group.shared_rg.name
  location            = azurerm_resource_group.shared_rg.location
  workspace_id        = module.log_analytics.resource_id
  application_type    = "web"
  tags                = local.tags
}


#################################################################################
# Container Registry
#################################################################################

module "container_registry" {
  source  = "Azure/avm-res-containerregistry-registry/azurerm"
  name                          = replace("${local.identifier}acr", "-", "")
  resource_group_name           = azurerm_resource_group.shared_rg.name
  location                      = azurerm_resource_group.shared_rg.location
  sku                           = "Standard"
  zone_redundancy_enabled       = false
  public_network_access_enabled = true
  admin_enabled                 = false
  tags                          = local.tags

  diagnostic_settings = {
    to_law = {
      name                  = "to-law"
      workspace_resource_id = module.log_analytics.resource_id
    }
  }
}


#################################################################################
# Key Vault for AI Foundry
#################################################################################

module "ai_keyvault" {
  source  = "Azure/avm-res-keyvault-vault/azurerm"
  name                              = "${local.identifier}-kv"
  resource_group_name               = azurerm_resource_group.shared_rg.name
  location                          = var.region_aifoundry
  tenant_id                         = data.azurerm_client_config.current.tenant_id
  sku_name                          = "standard"
  public_network_access_enabled     = true
  tags                              = local.tags

  diagnostic_settings = {
    to_law = {
      name                  = "to-law"
      workspace_resource_id = module.log_analytics.resource_id
    }
  }
}


#################################################################################
# Storage Account for Microsoft Foundry blob uploads and NL2SQL data
#################################################################################

module "ai_storage" {
  source  = "Azure/avm-res-storage-storageaccount/azurerm"
  name                          = replace("${local.identifier}foundry", "-", "")
  resource_group_name           = azurerm_resource_group.shared_rg.name
  location                      = var.region_aifoundry
  account_tier                  = "Standard"
  account_replication_type      = "LRS"
  public_network_access_enabled = true
  shared_access_key_enabled     = false
  tags                          = local.tags

  # Enable static website hosting for frontend
  static_website = {
    frontend = {
      index_document     = "index.html"
      error_404_document = "index.html"  # SPA fallback
    }
  }

  # Allow deployer IP and Azure services through the firewall
  network_rules = {
    default_action = "Deny"
    bypass         = ["AzureServices"]
    ip_rules       = [local.deployer_ip]
  }

  containers = {
    nl2sql = {
      name                  = "nl2sql"
      container_access_type = "private"
    }
  }

  # Role assignment for current user to upload blobs
  role_assignments = {
    storage_blob_contributor = {
      role_definition_id_or_name = "Storage Blob Data Contributor"
      principal_id               = data.azurerm_client_config.current.object_id
    }
  }
}

# Wait for storage RBAC to propagate before uploading blobs
resource "time_sleep" "wait_for_storage_rbac" {
  depends_on      = [module.ai_storage]
  create_duration = "60s"
}

# Upload query files from /data/queries
resource "azurerm_storage_blob" "nl2sql_queries" {
  for_each               = fileset("${path.module}/../../data/queries", "*.json")
  name                   = "queries/${each.value}"
  storage_account_name   = module.ai_storage.name
  storage_container_name = "nl2sql"
  type                   = "Block"
  source                 = "${path.module}/../../data/queries/${each.value}"
  content_type           = "application/json"

  depends_on = [time_sleep.wait_for_storage_rbac]
}

# Upload table schema files from /data/tables
resource "azurerm_storage_blob" "nl2sql_tables" {
  for_each               = fileset("${path.module}/../../data/tables", "*.json")
  name                   = "tables/${each.value}"
  storage_account_name   = module.ai_storage.name
  storage_container_name = "nl2sql"
  type                   = "Block"
  source                 = "${path.module}/../../data/tables/${each.value}"
  content_type           = "application/json"

  depends_on = [time_sleep.wait_for_storage_rbac]
}

# Upload query template files from /data/query_templates
resource "azurerm_storage_blob" "nl2sql_query_templates" {
  for_each               = fileset("${path.module}/../../data/query_templates", "*.json")
  name                   = "query_templates/${each.value}"
  storage_account_name   = module.ai_storage.name
  storage_container_name = "nl2sql"
  type                   = "Block"
  source                 = "${path.module}/../../data/query_templates/${each.value}"
  content_type           = "application/json"

  depends_on = [time_sleep.wait_for_storage_rbac]
}


#################################################################################
# Cosmos DB Account for Microsoft Foundry agent service thread storage
#################################################################################

module "ai_cosmosdb" {
  source  = "Azure/avm-res-documentdb-databaseaccount/azurerm"
  name                          = "${local.identifier}-foundry"
  resource_group_name           = azurerm_resource_group.shared_rg.name
  location                      = var.region_aifoundry
  public_network_access_enabled = true
  analytical_storage_enabled    = true
  automatic_failover_enabled    = true
  
  geo_locations = [
    {
      location          = var.region_aifoundry
      failover_priority = 0
      zone_redundant    = false
    }
  ]

  diagnostic_settings = {
    to_law = {
      name                  = "to-law"
      workspace_resource_id = module.log_analytics.resource_id
      metric_categories = ["SLI", "Requests"]
    }
  }

  tags = local.tags
}

# Cosmos DB Data Contributor role assignment for current user
resource "azurerm_cosmosdb_sql_role_assignment" "current_user" {
  resource_group_name = azurerm_resource_group.shared_rg.name
  account_name        = module.ai_cosmosdb.name
  # Built-in Data Contributor role: 00000000-0000-0000-0000-000000000002
  role_definition_id  = "${module.ai_cosmosdb.resource_id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002"
  principal_id        = data.azurerm_client_config.current.object_id
  scope               = module.ai_cosmosdb.resource_id
}

# Cosmos DB Data Contributor role assignment for the Foundry project managed identity
resource "azurerm_cosmosdb_sql_role_assignment" "foundry_project" {
  resource_group_name = azurerm_resource_group.shared_rg.name
  account_name        = module.ai_cosmosdb.name
  # Built-in Data Contributor role: 00000000-0000-0000-0000-000000000002
  role_definition_id  = "${module.ai_cosmosdb.resource_id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002"
  principal_id        = module.ai_foundry.ai_foundry_project_system_identity_principal_id["dataagent"]
  scope               = module.ai_cosmosdb.resource_id
}


#################################################################################
# AI Search - linked to Microsoft Foundry
#################################################################################

module "ai_search" {
  source  = "Azure/avm-res-search-searchservice/azurerm"
  name                          = "${local.identifier}"
  resource_group_name           = azurerm_resource_group.shared_rg.name
  location                      = var.region_aifoundry
  sku                           = "standard"
  public_network_access_enabled = true
  local_authentication_enabled  = true
  # Enable both API key and AAD authentication
  authentication_failure_mode   = "http401WithBearerChallenge"
  tags                          = local.tags

  # Allow deployer IP through firewall
  allowed_ips = [local.deployer_ip]

  # Enable managed identity for RBAC access to storage and AI services
  managed_identities = {
    system_assigned = true
  }

  # Role assignment for current user to manage search service
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
}


#################################################################################
# AI Foundry (Pattern Module)
#################################################################################

module "ai_foundry" {
  source  = "Azure/avm-ptn-aiml-ai-foundry/azurerm"
  version = "~> 0.8.0"

  base_name                  = local.identifier
  location                   = var.region_aifoundry
  resource_group_resource_id = azurerm_resource_group.shared_rg.id

  tags = local.tags

  # Disable BYOR creation - using existing resources via project connections only
  # Note: The *_definition blocks have bugs in AVM 0.8.0, so we skip them
  create_byor = false

  # AI Foundry configuration - enable agent service for thread storage in Cosmos DB
  ai_foundry = {
    create_ai_agent_service = true
  }

  # AI Projects configuration
  ai_projects = {
    dataagent = {
      name                       = "dataexplorer"
      display_name               = "Data Exploration"
      description                = "Data exploration agents and related resources"
      create_project_connections = true
      cosmos_db_connection = {
        existing_resource_id = module.ai_cosmosdb.resource_id
      }
      key_vault_connection = {
        existing_resource_id = module.ai_keyvault.resource_id
      }
      storage_account_connection = {
        existing_resource_id = module.ai_storage.resource_id
      }
      ai_search_connection = {
        existing_resource_id = module.ai_search.resource_id
      }
    }
  }

  # Model deployments are created separately below to avoid concurrency issues
  # with Azure AI Services API (see azapi_resource.ai_model_deployment_* resources)

  depends_on = [
    module.ai_keyvault,
    module.ai_storage,
    module.ai_cosmosdb
  ]
}


#################################################################################
# AI Model Deployments
# Created as separate resources with explicit depends_on to avoid concurrency
# issues with Azure AI Services API. This allows running terraform apply
# without the -parallelism=1 flag.
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

  # Sequential deployment to avoid Azure API concurrency issues
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

  # Sequential deployment to avoid Azure API concurrency issues
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

  # Sequential deployment to avoid Azure API concurrency issues
  depends_on = [azapi_resource.ai_model_deployment_embedding_small]
}


#################################################################################
# Application Insights Connection for AI Foundry Project
# Note: Not yet supported in AVM module. The azapi approach below is not working
# with the current API version. Add this connection manually via Azure Portal:
# AI Foundry Project -> Connections -> Add connection -> Application Insights
#################################################################################

# resource "azapi_resource" "ai_foundry_appinsights_connection" {
#   name      = module.application_insights.name
#   parent_id = module.ai_foundry.ai_foundry_project_id["dataagent"]
#   type      = "Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview"
#   body = {
#     properties = {
#       category = "ApplicationInsights"
#       target   = module.application_insights.resource_id
#       authType = "AAD"
#       metadata = {
#         ApiType    = "Azure"
#         ResourceId = module.application_insights.resource_id
#         location   = azurerm_resource_group.shared_rg.location
#       }
#     }
#   }
#   schema_validation_enabled = false
#   depends_on                = [module.ai_foundry]
# }


#################################################################################
# Azure SQL Database with Wide World Importers Sample Data
# The database is created empty, then WideWorldImporters BACPAC is imported
# via a post-deployment script (see null_resource.import_wideworldimporters)
#################################################################################

module "sql_server" {
  source  = "Azure/avm-res-sql-server/azurerm"
  name                = "${local.identifier}-sql"
  resource_group_name = azurerm_resource_group.shared_rg.name
  location            = azurerm_resource_group.shared_rg.location
  server_version      = "12.0"
  tags = local.tags

  # Use Entra ID authentication only (recommended)
  azuread_administrator = {
    azuread_authentication_only = true
    login_username              = data.azurerm_client_config.current.client_id
    object_id                   = data.azurerm_client_config.current.object_id
    tenant_id                   = data.azurerm_client_config.current.tenant_id
  }

  # Create empty database - WideWorldImporters will be imported separately
  databases = {
    wideworldimporters = {
      name        = "WideWorldImportersStd"
      sku_name    = "S0"
      max_size_gb = 250
    }
  }

  public_network_access_enabled = true
  
  # Allow Azure services to access the server
  firewall_rules = {
    allow_azure_services = {
      start_ip_address = "0.0.0.0"
      end_ip_address   = "0.0.0.0"
    }
    allow_deployer = {
      start_ip_address = local.deployer_ip
      end_ip_address   = local.deployer_ip
    }
  }

  # Note: SQL Server doesn't support diagnostic settings at the server level.
  # Use SQL Auditing or database-level diagnostic settings instead.
}


#################################################################################
# Import Wide World Importers BACPAC
# Downloads and imports the WideWorldImporters-Standard sample database from
# Microsoft's official release. This runs after the empty database is created.
# 
# IMPORTANT: This requires sqlpackage to be installed locally.
# Install via: dotnet tool install -g microsoft.sqlpackage
# Or download from: https://aka.ms/sqlpackage-linux
#
# Note: Import can take 5-10 minutes depending on database size.
#################################################################################

resource "null_resource" "import_wideworldimporters" {
  depends_on = [module.sql_server]

  triggers = {
    sql_server_name = module.sql_server.resource.name
    database_name   = "WideWorldImportersStd"
  }

  provisioner "local-exec" {
    interpreter = ["pwsh", "-Command"]
    command     = "& '${path.module}/../../scripts/import-wideworldimporters.ps1' -SqlServerName '${module.sql_server.resource.name}' -DatabaseName 'WideWorldImportersStd' -ResourceGroup '${azurerm_resource_group.shared_rg.name}' -Force"
  }
}


#################################################################################
# Search configuration
# Note: Search indexes, skillsets, indexers must be created via the Search Data
# Plane API (not ARM). We use null_resource with az rest/az role assignment to
# manage everything after resources are created.
#################################################################################

# Create Search data sources, indexes, skillsets, and indexers via REST API
# Also assigns RBAC roles for Search service managed identity
resource "null_resource" "search_config" {
  depends_on = [
    module.ai_search,
    module.ai_storage,
    module.ai_foundry,
    azurerm_storage_blob.nl2sql_queries,
    azurerm_storage_blob.nl2sql_tables,
    azurerm_storage_blob.nl2sql_query_templates
  ]

  triggers = {
    search_name      = module.ai_search.resource.name
    storage_id       = module.ai_storage.resource_id
    ai_foundry_id    = module.ai_foundry.ai_foundry_id
    ai_services_name = module.ai_foundry.ai_foundry_name
  }

  provisioner "local-exec" {
    command = <<-EOT
      set -e
      SEARCH_NAME="${module.ai_search.resource.name}"
      SEARCH_URL="https://$${SEARCH_NAME}.search.windows.net"
      API_VERSION="2024-05-01-preview"
      STORAGE_RESOURCE_ID="${module.ai_storage.resource_id}"
      AI_FOUNDRY_ID="${module.ai_foundry.ai_foundry_id}"
      AI_SERVICES_NAME="${module.ai_foundry.ai_foundry_name}"
      
      # Get Search service principal ID
      echo "Getting Search service managed identity principal ID..."
      SEARCH_PRINCIPAL_ID=$(az search service show --name "$${SEARCH_NAME}" --resource-group "${azurerm_resource_group.shared_rg.name}" --query identity.principalId -o tsv)
      
      if [ -z "$${SEARCH_PRINCIPAL_ID}" ] || [ "$${SEARCH_PRINCIPAL_ID}" = "null" ]; then
        echo "Error: Search service does not have a managed identity"
        exit 1
      fi
      echo "Search principal ID: $${SEARCH_PRINCIPAL_ID}"
      
      # Assign Storage Blob Data Reader role
      echo "Assigning Storage Blob Data Reader role to Search service..."
      az role assignment create \
        --role "Storage Blob Data Reader" \
        --assignee-object-id "$${SEARCH_PRINCIPAL_ID}" \
        --assignee-principal-type ServicePrincipal \
        --scope "$${STORAGE_RESOURCE_ID}" \
        --only-show-errors || echo "Role may already exist"
      
      # Assign Cognitive Services OpenAI User role
      echo "Assigning Cognitive Services OpenAI User role to Search service..."
      az role assignment create \
        --role "Cognitive Services OpenAI User" \
        --assignee-object-id "$${SEARCH_PRINCIPAL_ID}" \
        --assignee-principal-type ServicePrincipal \
        --scope "$${AI_FOUNDRY_ID}" \
        --only-show-errors || echo "Role may already exist"
      
      # Wait for RBAC to propagate
      echo "Waiting 60 seconds for RBAC propagation..."
      sleep 60
      
      echo "Getting access token for Search..."
      TOKEN=$(az account get-access-token --resource https://search.azure.com --query accessToken -o tsv)
      
      echo "Creating data source: agentic-queries..."
      curl -s -X PUT "$${SEARCH_URL}/datasources/agentic-queries?api-version=$${API_VERSION}" \
        -H "Authorization: Bearer $${TOKEN}" \
        -H "Content-Type: application/json" \
        -d '{
          "name": "agentic-queries",
          "type": "azureblob",
          "credentials": {
            "connectionString": "ResourceId='"$${STORAGE_RESOURCE_ID}"';"
          },
          "container": {
            "name": "nl2sql",
            "query": "queries"
          }
        }'
      
      echo ""
      echo "Creating data source: agentic-tables..."
      curl -s -X PUT "$${SEARCH_URL}/datasources/agentic-tables?api-version=$${API_VERSION}" \
        -H "Authorization: Bearer $${TOKEN}" \
        -H "Content-Type: application/json" \
        -d '{
          "name": "agentic-tables",
          "type": "azureblob",
          "credentials": {
            "connectionString": "ResourceId='"$${STORAGE_RESOURCE_ID}"';"
          },
          "container": {
            "name": "nl2sql",
            "query": "tables"
          }
        }'
      
      echo ""
      echo "Creating data source: agentic-query-templates..."
      curl -s -X PUT "$${SEARCH_URL}/datasources/agentic-query-templates?api-version=$${API_VERSION}" \
        -H "Authorization: Bearer $${TOKEN}" \
        -H "Content-Type: application/json" \
        -d '{
          "name": "agentic-query-templates",
          "type": "azureblob",
          "credentials": {
            "connectionString": "ResourceId='"$${STORAGE_RESOURCE_ID}"';"
          },
          "container": {
            "name": "nl2sql",
            "query": "query_templates"
          }
        }'
      
      echo ""
      echo "Creating index: queries..."
      curl -s -X PUT "$${SEARCH_URL}/indexes/queries?api-version=$${API_VERSION}" \
        -H "Authorization: Bearer $${TOKEN}" \
        -H "Content-Type: application/json" \
        -d @${path.module}/../search-config/queries_index.json
      
      echo ""
      echo "Creating index: tables..."
      curl -s -X PUT "$${SEARCH_URL}/indexes/tables?api-version=$${API_VERSION}" \
        -H "Authorization: Bearer $${TOKEN}" \
        -H "Content-Type: application/json" \
        -d @${path.module}/../search-config/tables_index.json
      
      echo ""
      echo "Creating index: query_templates..."
      curl -s -X PUT "$${SEARCH_URL}/indexes/query_templates?api-version=$${API_VERSION}" \
        -H "Authorization: Bearer $${TOKEN}" \
        -H "Content-Type: application/json" \
        -d @${path.module}/../search-config/query_templates_index.json
      
      echo ""
      echo "Creating skillset: query-embed-skill..."
      curl -s -X PUT "$${SEARCH_URL}/skillsets/query-embed-skill?api-version=$${API_VERSION}" \
        -H "Authorization: Bearer $${TOKEN}" \
        -H "Content-Type: application/json" \
        -d '{
          "name": "query-embed-skill",
          "description": "OpenAI Embedding skill for queries",
          "skills": [
            {
              "@odata.type": "#Microsoft.Skills.Text.AzureOpenAIEmbeddingSkill",
              "name": "vector-embed-field-question",
              "description": "vector embedding for the question field",
              "context": "/document",
              "resourceUri": "https://'"$${AI_SERVICES_NAME}"'.openai.azure.com",
              "deploymentId": "embedding-large",
              "dimensions": 3072,
              "modelName": "text-embedding-3-large",
              "inputs": [
                {
                  "name": "text",
                  "source": "/document/question"
                }
              ],
              "outputs": [
                {
                  "name": "embedding",
                  "targetName": "content_embeddings"
                }
              ]
            }
          ]
        }'
      
      echo ""
      echo "Creating skillset: table-embed-skill..."
      curl -s -X PUT "$${SEARCH_URL}/skillsets/table-embed-skill?api-version=$${API_VERSION}" \
        -H "Authorization: Bearer $${TOKEN}" \
        -H "Content-Type: application/json" \
        -d '{
          "name": "table-embed-skill",
          "description": "OpenAI Embedding skill for table descriptions",
          "skills": [
            {
              "@odata.type": "#Microsoft.Skills.Text.AzureOpenAIEmbeddingSkill",
              "name": "vector-embed-field-description",
              "description": "vector embedding for the description field",
              "context": "/document",
              "resourceUri": "https://'"$${AI_SERVICES_NAME}"'.openai.azure.com",
              "deploymentId": "embedding-large",
              "dimensions": 3072,
              "modelName": "text-embedding-3-large",
              "inputs": [
                {
                  "name": "text",
                  "source": "/document/description"
                }
              ],
              "outputs": [
                {
                  "name": "embedding",
                  "targetName": "content_embeddings"
                }
              ]
            }
          ]
        }'
      
      echo ""
      echo "Creating skillset: query-template-embed-skill..."
      curl -s -X PUT "$${SEARCH_URL}/skillsets/query-template-embed-skill?api-version=$${API_VERSION}" \
        -H "Authorization: Bearer $${TOKEN}" \
        -H "Content-Type: application/json" \
        -d '{
          "name": "query-template-embed-skill",
          "description": "OpenAI Embedding skill for query template questions",
          "skills": [
            {
              "@odata.type": "#Microsoft.Skills.Text.AzureOpenAIEmbeddingSkill",
              "name": "vector-embed-field-question",
              "description": "vector embedding for the question field",
              "context": "/document",
              "resourceUri": "https://'"$${AI_SERVICES_NAME}"'.openai.azure.com",
              "deploymentId": "embedding-large",
              "dimensions": 3072,
              "modelName": "text-embedding-3-large",
              "inputs": [
                {
                  "name": "text",
                  "source": "/document/question"
                }
              ],
              "outputs": [
                {
                  "name": "embedding",
                  "targetName": "content_embeddings"
                }
              ]
            }
          ]
        }'
      
      echo ""
      echo "Creating indexer: indexer-queries..."
      curl -s -X PUT "$${SEARCH_URL}/indexers/indexer-queries?api-version=$${API_VERSION}" \
        -H "Authorization: Bearer $${TOKEN}" \
        -H "Content-Type: application/json" \
        -d '{
          "name": "indexer-queries",
          "dataSourceName": "agentic-queries",
          "skillsetName": "query-embed-skill",
          "targetIndexName": "queries",
          "parameters": {
            "configuration": {
              "dataToExtract": "contentAndMetadata",
              "parsingMode": "json"
            }
          },
          "fieldMappings": [],
          "outputFieldMappings": [
            {
              "sourceFieldName": "/document/content_embeddings",
              "targetFieldName": "content_vector"
            }
          ]
        }'
      
      echo ""
      echo "Creating indexer: indexer-tables..."
      curl -s -X PUT "$${SEARCH_URL}/indexers/indexer-tables?api-version=$${API_VERSION}" \
        -H "Authorization: Bearer $${TOKEN}" \
        -H "Content-Type: application/json" \
        -d '{
          "name": "indexer-tables",
          "dataSourceName": "agentic-tables",
          "skillsetName": "table-embed-skill",
          "targetIndexName": "tables",
          "parameters": {
            "configuration": {
              "dataToExtract": "contentAndMetadata",
              "parsingMode": "json"
            }
          },
          "fieldMappings": [],
          "outputFieldMappings": [
            {
              "sourceFieldName": "/document/content_embeddings",
              "targetFieldName": "content_vector"
            }
          ]
        }'
      
      echo ""
      echo "Creating indexer: indexer-query-templates..."
      curl -s -X PUT "$${SEARCH_URL}/indexers/indexer-query-templates?api-version=$${API_VERSION}" \
        -H "Authorization: Bearer $${TOKEN}" \
        -H "Content-Type: application/json" \
        -d '{
          "name": "indexer-query-templates",
          "dataSourceName": "agentic-query-templates",
          "skillsetName": "query-template-embed-skill",
          "targetIndexName": "query_templates",
          "parameters": {
            "configuration": {
              "dataToExtract": "contentAndMetadata",
              "parsingMode": "json"
            }
          },
          "fieldMappings": [],
          "outputFieldMappings": [
            {
              "sourceFieldName": "/document/content_embeddings",
              "targetFieldName": "content_vector"
            }
          ]
        }'
      
      echo ""
      echo "Search configuration complete!"
    EOT
  }
}


#################################################################################
# Container App Environment for Backend API
#################################################################################

module "container_app_environment" {
  source  = "Azure/avm-res-app-managedenvironment/azurerm"
  version = "~> 0.2"

  name                           = "${local.identifier}-cae"
  resource_group_name            = azurerm_resource_group.shared_rg.name
  location                       = azurerm_resource_group.shared_rg.location
  log_analytics_workspace = {
    resource_id = module.log_analytics.resource_id
  }
  zone_redundancy_enabled        = false
  infrastructure_subnet_id       = null  # Use consumption plan without VNet integration
  internal_load_balancer_enabled = false
  tags                           = local.tags
}


#################################################################################
# Container App for Backend API
#################################################################################

# Get the AI Foundry hub properties to extract the endpoint
data "azapi_resource" "ai_foundry_hub" {
  type                   = "Microsoft.CognitiveServices/accounts@2024-10-01"
  resource_id            = module.ai_foundry.ai_foundry_id
  response_export_values = ["properties.endpoint"]
}

locals {
  # The endpoint property gives us the hub URL directly
  # Format: https://<hub-name>-<random>.services.ai.azure.com/
  ai_hub_endpoint     = data.azapi_resource.ai_foundry_hub.output.properties.endpoint
  ai_project_name     = module.ai_foundry.ai_foundry_project_name["dataagent"]
  ai_project_endpoint = "${trimsuffix(local.ai_hub_endpoint, "/")}/api/projects/${local.ai_project_name}"
}



resource "azurerm_user_assigned_identity" "api_identity" {
  name                = "${local.identifier}-api-identity"
  resource_group_name = azurerm_resource_group.shared_rg.name
  location            = azurerm_resource_group.shared_rg.location
  tags                = local.tags
}

# Grant API identity access to ACR
resource "azurerm_role_assignment" "api_acr_pull" {
  scope                = module.container_registry.resource_id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.api_identity.principal_id
}

# Grant API identity access to AI Foundry
resource "azurerm_role_assignment" "api_ai_foundry" {
  scope                = module.ai_foundry.ai_foundry_project_id["dataagent"]
  role_definition_name = "Azure AI Developer"
  principal_id         = azurerm_user_assigned_identity.api_identity.principal_id
}

# Grant API identity access to AI Search
resource "azurerm_role_assignment" "api_search" {
  scope                = module.ai_search.resource_id
  role_definition_name = "Search Index Data Reader"
  principal_id         = azurerm_user_assigned_identity.api_identity.principal_id
}

# Grant API identity access to SQL Database
resource "azurerm_role_assignment" "api_sql" {
  scope                = module.sql_server.resource_id
  role_definition_name = "Contributor"
  principal_id         = azurerm_user_assigned_identity.api_identity.principal_id
}

# Grant API identity access to Storage
resource "azurerm_role_assignment" "api_storage" {
  scope                = module.ai_storage.resource_id
  role_definition_name = "Storage Blob Data Reader"
  principal_id         = azurerm_user_assigned_identity.api_identity.principal_id
}

# Grant API identity access to Cosmos DB
resource "azurerm_role_assignment" "api_cosmos" {
  scope                = module.ai_cosmosdb.resource_id
  role_definition_name = "Cosmos DB Account Reader Role"
  principal_id         = azurerm_user_assigned_identity.api_identity.principal_id
}

resource "azurerm_container_app" "api" {
  name                         = "${local.identifier}-api"
  resource_group_name          = azurerm_resource_group.shared_rg.name
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

    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }

  template {
    min_replicas = 0
    max_replicas = 3

    container {
      name   = "api"
      image  = "${module.container_registry.resource.login_server}/dataagent-api:latest"
      cpu    = 1.0
      memory = "2Gi"

      env {
        name  = "AZURE_AD_TENANT_ID"
        value = data.azurerm_client_config.current.tenant_id
      }
      env {
        name  = "AZURE_AD_CLIENT_ID"
        value = azurerm_user_assigned_identity.api_identity.client_id
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
        name  = "AZURE_SEARCH_INDEX_QUERIES"
        value = "queries"
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
        value = "WideWorldImporters"
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
    }
  }

  depends_on = [
    azurerm_role_assignment.api_acr_pull,
    azurerm_role_assignment.api_ai_foundry,
    azurerm_role_assignment.api_search,
    azurerm_role_assignment.api_storage
  ]
}
