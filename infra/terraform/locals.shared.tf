locals {
  acr_is_private = var.acr_build_mode == "private"

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
