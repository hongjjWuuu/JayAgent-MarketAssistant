param(
    [Parameter(Mandatory=$true)][string]$ServerAddress,
    [Parameter(Mandatory=$true)][string]$CertificatePath
)

$ErrorActionPreference = "Stop"
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)
if (-not $isAdmin) { throw "Run this client setup script in an elevated PowerShell session" }
$parsedAddress = $null
if (-not [System.Net.IPAddress]::TryParse($ServerAddress, [ref]$parsedAddress)) {
    throw "ServerAddress must be an IPv4 or IPv6 address"
}
$certificate = Resolve-Path -LiteralPath $CertificatePath
$hostsPath = "$env:SystemRoot\System32\drivers\etc\hosts"
foreach ($hostName in "marketassistant.test", "api.marketassistant.test", "auth.marketassistant.test") {
    if (-not (Select-String -LiteralPath $hostsPath -SimpleMatch $hostName -Quiet)) {
        Add-Content -LiteralPath $hostsPath -Value "$ServerAddress`t$hostName"
    }
}
Import-Certificate -FilePath $certificate -CertStoreLocation Cert:\LocalMachine\Root | Out-Null
Write-Host "M4 client configured. Open https://marketassistant.test" -ForegroundColor Green
