#################################################################################
# Utility VM
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
    storage_account_type = "Standard_LRS"
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
