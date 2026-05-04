variable "subscription_id" {
  type        = string
  description = "Azure subscription ID used for deployment."
}

variable "region" {
  type        = string
  default     = "westus3"
  description = "Azure region for private networking resources."
}

variable "region_aifoundry" {
  type        = string
  default     = "westus3"
  description = "Azure region to deploy AI Foundry resources."
}

variable "region_search" {
  type        = string
  default     = "eastus"
  description = "Azure region to deploy AI Search resources."
}

variable "frontend_app_client_id" {
  type        = string
  description = "Azure AD App Registration client ID for frontend authentication."
}

variable "azure_ad_allowed_tenant_ids" {
  type        = list(string)
  default     = []
  description = "Optional additional Azure AD tenant IDs allowed to authenticate to the API. The deployment tenant is always included automatically."
}

variable "github_federated_principal_object_id" {
  type        = string
  default     = null
  description = "Optional object ID of the GitHub OIDC federated service principal used by CI/CD and private runner workflows. This must be the Enterprise Application (service principal) object ID, not the App Registration object ID."
}

variable "github_federated_principal_client_id" {
  type        = string
  default     = null
  description = "Optional client ID (application ID) for the GitHub OIDC federated principal. Used as SQL Entra admin login_username fallback when sql_azuread_admin_login_username is not provided."
}

variable "github_runner_identity_name" {
  type        = string
  default     = "cadence-gh-runner-id"
  description = "User-assigned managed identity name used by the private GitHub runner Container Apps job."
}

variable "sql_azuread_admin_object_id" {
  type        = string
  default     = null
  description = "Optional object ID to configure as Azure SQL Entra administrator. Defaults to github_federated_principal_object_id when set, otherwise current deployment principal object ID."
}

variable "sql_azuread_admin_login_username" {
  type        = string
  default     = null
  description = "Optional login username for Azure SQL Entra administrator. Defaults to github_federated_principal_client_id when set, otherwise current deployment principal client ID."
}

variable "enable_sql_server_directory_readers_grant" {
  type        = bool
  default     = true
  description = "When true, Terraform ensures the Azure SQL server managed identity is in the Entra Directory Readers role so CREATE USER FROM EXTERNAL PROVIDER can resolve service principals."
}

variable "name_prefix" {
  type        = string
  default     = "cadence"
  description = "Prefix used in resource names."
}

variable "resource_group_name" {
  type        = string
  default     = null
  description = "Optional resource group name override. If null, a generated name is used."
}

variable "vnet_address_space" {
  type        = list(string)
  default     = ["10.40.0.0/16"]
  description = "Address space assigned to the private virtual network."
}

variable "private_endpoints_subnet_cidr" {
  type        = string
  default     = "10.40.0.0/24"
  description = "CIDR for the private endpoints subnet."
}

variable "application_subnet_cidr" {
  type        = string
  default     = "10.40.1.0/24"
  description = "CIDR for the application workload subnet."
}

variable "container_apps_subnet_cidr" {
  type        = string
  default     = "10.40.3.0/23"
  description = "CIDR for the Container Apps managed environment subnet."
}

variable "ai_agent_services_subnet_cidr" {
  type        = string
  default     = "10.40.5.0/24"
  description = "CIDR for the AI Foundry Agent Service network injection subnet."
}

variable "utility_subnet_cidr" {
  type        = string
  default     = "10.40.4.0/24"
  description = "CIDR for the utility subnet hosting general-purpose IaaS compute."
}

variable "bastion_subnet_cidr" {
  type        = string
  default     = "10.40.6.0/26"
  description = "CIDR for AzureBastionSubnet (must be /26 or larger address space)."
}

variable "data_subnet_cidr" {
  type        = string
  default     = "10.40.2.0/24"
  description = "CIDR for the data tier subnet."
}

variable "utility_vm_admin_password" {
  type        = string
  description = "Admin password for the utility VM local administrator account."
  sensitive   = true
}

variable "utility_vm_size" {
  type        = string
  default     = "Standard_D4s_v3"
  description = "Azure VM size for the utility VM."
}

variable "private_dns_zone_names" {
  type        = set(string)
  description = "Private DNS zones created and linked to the VNet."
  default = [
    "privatelink.azurecr.io",
    "privatelink.blob.core.windows.net",
    "privatelink.openai.azure.com",
    "privatelink.cognitiveservices.azure.com",
    "privatelink.services.ai.azure.com",
    "privatelink.database.windows.net",
    "privatelink.documents.azure.com",
    "privatelink.search.windows.net",
    "privatelink.vaultcore.azure.net"
  ]
}

variable "sql_database_name" {
  type        = string
  default     = "WideWorldImportersStd"
  description = "Azure SQL database name for NL2SQL data."
}

variable "enable_local_exec_provisioning" {
  type        = bool
  default     = false
  description = "Whether to run legacy local-exec SQL import from Terraform. Prefer Phase 2 GitHub private-runner workflows for private data-plane provisioning (storage, Search, and SQL)."
}

variable "private_endpoints" {
  type = map(object({
    resource_id       = string
    subresource_names = list(string)
    dns_zone_name     = string
  }))
  default     = {}
  description = "Optional private endpoints keyed by endpoint name."
}

variable "tags" {
  type        = map(string)
  default     = {}
  description = "Optional tags merged with module defaults."
}

variable "acr_build_mode" {
  type        = string
  default     = "public"
  description = "ACR build execution mode: public uses Azure-hosted ACR Tasks; private uses the private ACR agent pool."

  validation {
    condition     = contains(["public", "private"], var.acr_build_mode)
    error_message = "acr_build_mode must be either 'public' or 'private'."
  }
}

variable "foundry_eval_name" {
  type        = string
  default     = "cadence-eval-v1"
  description = "Stable Foundry evaluation definition name reused across scheduled runs."
}

variable "foundry_dataset_name" {
  type        = string
  default     = "cadence-eval-gold"
  description = "Foundry dataset asset name used by evaluation runs."
}

variable "foundry_dataset_version" {
  type        = string
  default     = "v1"
  description = "Foundry dataset asset version used by evaluation runs."
}
