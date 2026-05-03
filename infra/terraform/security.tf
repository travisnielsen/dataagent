#################################################################################
# Security and Access Control
#################################################################################

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

resource "azurerm_role_assignment" "github_federated_ai_foundry_user" {
  count = local.github_federated_rbac_principal_object_id != "" ? 1 : 0

  scope                = module.ai_foundry.ai_foundry_project_id["cadence"]
  role_definition_name = "Azure AI User"
  principal_id         = local.github_federated_rbac_principal_object_id
}

resource "time_sleep" "github_runner_identity_propagation" {
  depends_on      = [azurerm_user_assigned_identity.github_runner]
  create_duration = "30s"
}

resource "azurerm_role_assignment" "github_runner_acr_pull" {
  scope                = module.container_registry.resource_id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.github_runner.principal_id

  depends_on = [time_sleep.github_runner_identity_propagation]
}

resource "azurerm_role_assignment" "github_runner_ai_foundry_user" {
  scope                = module.ai_foundry.ai_foundry_project_id["cadence"]
  role_definition_name = "Azure AI User"
  principal_id         = azurerm_user_assigned_identity.github_runner.principal_id

  depends_on = [time_sleep.github_runner_identity_propagation]
}
