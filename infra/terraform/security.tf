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

  # IMPORTANT: Use Foundry account scope (not project scope).
  #
  # Symptom when mis-scoped: eval run is accepted and remains in_progress, but
  # result_counts.total stays 0 and output_items stays 0 indefinitely.
  #
  # Reason: the async evaluator executes under the submitting principal and needs
  # access to account-level model deployments (judge model). Project-only scope
  # can allow submission while still blocking actual evaluation execution.
  scope                = module.ai_foundry.ai_foundry_id
  role_definition_name = "Azure AI User"
  principal_id         = local.github_federated_rbac_principal_object_id
}

resource "azurerm_role_assignment" "github_federated_openai_user" {
  count = local.github_federated_rbac_principal_object_id != "" ? 1 : 0

  # Required data-plane permission for inference with Entra ID.
  #
  # Azure AI User alone is not sufficient for OpenAI inference calls made during
  # eval judging. This role provides least-privilege access needed for model
  # invocation without granting deployment management permissions.
  scope                = module.ai_foundry.ai_foundry_id
  role_definition_name = "Cognitive Services OpenAI User"
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
  # Keep runner identity aligned with federated identity behavior.
  # Account scope prevents submission-only access patterns for eval jobs.
  scope                = module.ai_foundry.ai_foundry_id
  role_definition_name = "Azure AI User"
  principal_id         = azurerm_user_assigned_identity.github_runner.principal_id

  depends_on = [time_sleep.github_runner_identity_propagation]
}

resource "azurerm_role_assignment" "github_runner_openai_user" {
  # Same least-privilege OpenAI inference access as federated principal.
  # Ensures both identities can invoke judge deployments during evaluation.
  scope                = module.ai_foundry.ai_foundry_id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = azurerm_user_assigned_identity.github_runner.principal_id

  depends_on = [time_sleep.github_runner_identity_propagation]
}

resource "azurerm_role_assignment" "github_runner_ai_developer_rg" {
  # REQUIRED for the runner UAMI to submit Foundry cloud evaluation runs and
  # have them complete. The Foundry evaluation engine uploads results via the
  # AzureML data-plane endpoint `evaluations/runs:updateUpload`, which is gated
  # by `Microsoft.MachineLearningServices/workspaces/evaluations/*` actions.
  # Those actions are part of the "Azure AI Developer" role; "Azure AI User"
  # alone is NOT sufficient and runs fail with PermissionDenied.
  #
  # Scope is the resource group so the grant covers the Foundry account, its
  # projects, and any associated AzureML/storage data-plane resources used by
  # the eval engine.
  scope                = azurerm_resource_group.private_rg.id
  role_definition_name = "Azure AI Developer"
  principal_id         = azurerm_user_assigned_identity.github_runner.principal_id

  depends_on = [time_sleep.github_runner_identity_propagation]
}

resource "azurerm_role_assignment" "github_runner_ai_foundry_user_project" {
  # Project-scope Azure AI User mirrors the typical Foundry RBAC pattern for
  # principals that interact with a specific project (datasets, evals,
  # threads). Account-scope grant above provides cross-project capability;
  # this project-scope grant ensures the SDK's project-scoped resolver finds
  # the principal when project-only checks are performed.
  scope                = module.ai_foundry.ai_foundry_project_id["cadence"]
  role_definition_name = "Azure AI User"
  principal_id         = azurerm_user_assigned_identity.github_runner.principal_id

  depends_on = [time_sleep.github_runner_identity_propagation]
}
