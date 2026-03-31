<#
.SYNOPSIS
    Creates Windows Services with auto-restart configuration
#>

param(
    [Parameter(Mandatory=$true)]
    [string]$ServiceName,
    
    [Parameter(Mandatory=$true)]
    [ValidateSet("Web", "Sync")]
    [string]$Type
)

$InstallPath = "C:\UserValidation"

# Service configuration
$serviceConfig = @{
    Web = @{
        DisplayName = "User Validation Web Service"
        Description = "Web interface for user validation system"
        ScriptPath = "$InstallPath\app\main.py"
        PythonArgs = "main.py"
    }
    Sync = @{
        DisplayName = "User Validation AD Sync Service"
        Description = "Background service for Active Directory synchronization"
        ScriptPath = "$InstallPath\app\ad_sync.py"
        PythonArgs = "ad_sync.py"
    }
}

$config = $serviceConfig[$Type]

# Create NSSM wrapper (Non-Sucking Service Manager - better than native Windows services for Python)
Write-Host "Installing NSSM..." -ForegroundColor Cyan
$nssmPath = "$InstallPath\scripts\nssm.exe"

if (!(Test-Path $nssmPath)) {
    Invoke-WebRequest -Uri "https://nssm.cc/release/nssm-2.24.zip" -OutFile "$env:TEMP\nssm.zip"
    Expand-Archive -Path "$env:TEMP\nssm.zip" -DestinationPath "$env:TEMP\nssm" -Force
    Copy-Item "$env:TEMP\nssm\nssm-2.24\win64\nssm.exe" -Destination $nssmPath
}

# Remove existing service if present
$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($existing) {
    Stop-Service -Name $ServiceName -Force
    & $nssmPath remove $ServiceName confirm
    Start-Sleep -Seconds 2
}

# Install service
$pythonPath = (Get-Command python).Source
& $nssmPath install $ServiceName $pythonPath $config.ScriptPath

# Configure service
& $nssmPath set $ServiceName DisplayName $config.DisplayName
& $nssmm set $ServiceName Description $config.Description
& $nssmm set $ServiceName AppDirectory $InstallPath\app
& $nssmm set $ServiceName AppStdout $InstallPath\logs\$Type-service.log
& $nssmm set $ServiceName AppStderr $InstallPath\logs\$Type-error.log
& $nssmm set $ServiceName AppRotateFiles 1
& $nssmm set $ServiceName AppRotateBytes 10485760  # 10MB

# Auto-restart configuration
& $nssmm set $ServiceName AppExit Default Restart
& $nssmm set $ServiceName AppRestartDelay 5000  # 5 seconds
& $nssmm set $ServiceName AppThrottle 10000  # Don't restart more than once per 10 seconds

# Start automatically
& $nssmm set $ServiceName Start SERVICE_AUTO_START

Write-Host "Service $ServiceName created successfully" -ForegroundColor Green