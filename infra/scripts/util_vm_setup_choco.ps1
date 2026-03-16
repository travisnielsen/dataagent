$markerDirectory = "C:\AzureData"
$markerPath = Join-Path $markerDirectory "util_vm_setup_choco.phase1.done"

$ProgressPreference = 'SilentlyContinue'

if (Test-Path -Path $markerPath) {
    Write-Output "Utility VM bootstrap already completed. Skipping phase 1 setup."
    exit 0
}

# Enable Hyper-V
Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V-All -All -NoRestart

# Install Chocolatey (works in SYSTEM context unlike winget)
Set-ExecutionPolicy Bypass -Scope Process -Force
[System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072
Invoke-Expression ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))

# Refresh environment to get choco command
$env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")

# Install software using Chocolatey
choco install powershell-core -y
choco install azure-cli -y
choco install azure-functions-core-tools -y
choco install git -y
choco install vscode -y
choco install python313 -y
choco install gh -y
choco install docker-desktop -y

# Install PowerShell modules
Install-PackageProvider -Name NuGet -MinimumVersion 2.8.5.201 -Force -ForceBootstrap
Set-PSRepository -Name PSGallery -InstallationPolicy Trusted
Install-Module -Name PowerShellGet -Force -AllowClobber -SkipPublisherCheck -Confirm:$false
Install-Module -Name Az -Repository PSGallery -Force -SkipPublisherCheck -Confirm:$false

# Persist marker before reboot so reruns stay idempotent.
New-Item -ItemType Directory -Path $markerDirectory -Force | Out-Null
Set-Content -Path $markerPath -Value "Completed $(Get-Date -Format o)" -Encoding UTF8

# Wait 60 seconds and re-start the computer
Start-Sleep -Seconds 60
Restart-Computer -Force

