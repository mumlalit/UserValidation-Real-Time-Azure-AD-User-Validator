<#
.SYNOPSIS
    Installation for Real-Time User Validation System
#>

param(
    [string]$ServerIP = "10.228.176.17",
    [int]$Port = 8080,
    [string]$InstallPath = "C:\UserValidation"
)

Write-Host "═══════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  User Validation System - Installation" -ForegroundColor Cyan
Write-Host "  Real-Time AD Validation" -ForegroundColor Cyan
Write-Host "═══════════════════════════════════════════" -ForegroundColor Cyan

function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $color = switch($Level) {
        "ERROR" { "Red" }
        "SUCCESS" { "Green" }
        "WARNING" { "Yellow" }
        default { "White" }
    }
    Write-Host "[$timestamp] [$Level] $Message" -ForegroundColor $color
}

try {
    # 1. Create directory structure
    Write-Log "Creating directory structure..." "INFO"
    $dirs = @(
        "$InstallPath\app",
        "$InstallPath\app\templates",
        "$InstallPath\app\static",
        "$InstallPath\data",
        "$InstallPath\logs",
        "$InstallPath\uploads",
        "$InstallPath\reports",
        "$InstallPath\scripts",
        "$InstallPath\config"
    )
    
    foreach ($dir in $dirs) {
        if (!(Test-Path $dir)) {
            New-Item -Path $dir -ItemType Directory -Force | Out-Null
        }
    }
    Write-Log "Directories created" "SUCCESS"

    # 2. Install Python dependencies
    Write-Log "Installing Python dependencies..." "INFO"
    Set-Location $InstallPath
    
    @"
Flask==3.0.0
Flask-SocketIO==5.3.5
openpyxl==3.1.2
pandas==2.1.4
python-dotenv==1.0.0
msal==1.26.0
requests==2.31.0
waitress==2.1.2
python-socketio==5.10.0
eventlet==0.33.3
"@ | Out-File -FilePath "$InstallPath\requirements.txt" -Encoding UTF8

    python -m pip install --upgrade pip
    pip install -r requirements.txt
    Write-Log "Dependencies installed" "SUCCESS"

    # 3. Firewall rule
    Write-Log "Configuring firewall..." "INFO"
    $ruleName = "UserValidation-Web-$Port"
    $existingRule = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
    if (!$existingRule) {
        New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow | Out-Null
    }
    Write-Log "Firewall configured" "SUCCESS"

    # 4. Create configuration
    Write-Log "Creating configuration..." "INFO"
    
    $appConfig = @{
        server_ip = $ServerIP
        port = $Port
        install_path = $InstallPath
        log_retention_days = 90
        max_upload_size_mb = 50
        admin_email = "admin@yourdomain.com"
        batch_size = 10
        max_concurrent_queries = 5
    }
    
    $appConfig | ConvertTo-Json | Out-File "$InstallPath\config\app_config.json" -Encoding UTF8
    Write-Log "Configuration created" "SUCCESS"

    # 5. Download NSSM for service management
    Write-Log "Installing NSSM..." "INFO"
    $nssmPath = "$InstallPath\scripts\nssm.exe"
    if (!(Test-Path $nssmPath)) {
        Invoke-WebRequest -Uri "https://nssm.cc/release/nssm-2.24.zip" -OutFile "$env:TEMP\nssm.zip"
        Expand-Archive -Path "$env:TEMP\nssm.zip" -DestinationPath "$env:TEMP\nssm" -Force
        Copy-Item "$env:TEMP\nssm\nssm-2.24\win64\nssm.exe" -Destination $nssmPath
    }

    # 6. Create Windows Service
    Write-Log "Creating Windows Service..." "INFO"
    
    $serviceName = "UserValidationWeb"
    $existing = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
    if ($existing) {
        Stop-Service -Name $serviceName -Force
        & $nssmPath remove $serviceName confirm
        Start-Sleep -Seconds 2
    }

    $pythonPath = (Get-Command python).Source
    & $nssmPath install $serviceName $pythonPath "$InstallPath\app\main.py"
    & $nssmPath set $serviceName DisplayName "User Validation Web Service"
    & $nssmPath set $serviceName Description "Real-time user validation against Active Directory"
    & $nssmPath set $serviceName AppDirectory "$InstallPath\app"
    & $nssmPath set $serviceName AppStdout "$InstallPath\logs\service.log"
    & $nssmPath set $serviceName AppStderr "$InstallPath\logs\error.log"
    & $nssmPath set $serviceName AppRotateFiles 1
    & $nssmPath set $serviceName AppRotateBytes 10485760
    & $nssmPath set $serviceName AppExit Default Restart
    & $nssmPath set $serviceName AppRestartDelay 5000
    & $nssmPath set $serviceName AppThrottle 10000
    & $nssmPath set $serviceName Start SERVICE_AUTO_START
    
    Write-Log "Service created" "SUCCESS"

    # 7. Setup health monitoring
    Write-Log "Setting up health monitoring..." "INFO"
    $action = New-ScheduledTaskAction -Execute "PowerShell.exe" -Argument "-File `"$InstallPath\scripts\health-check.ps1`""
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5)
    Register-ScheduledTask -TaskName "UserValidation-HealthCheck" -Action $action -Trigger $trigger -RunLevel Highest -Force | Out-Null
    Write-Log "Health monitoring configured" "SUCCESS"

    # 8. Start service
    Write-Log "Starting service..." "INFO"
    Start-Service -Name $serviceName
    Start-Sleep -Seconds 5
    Write-Log "Service started" "SUCCESS"

    Write-Host ""
    Write-Host "═══════════════════════════════════════════" -ForegroundColor Green
    Write-Host "  Installation Complete!" -ForegroundColor Green
    Write-Host "═══════════════════════════════════════════" -ForegroundColor Green
    Write-Host ""
    Write-Host "Access: http://$ServerIP`:$Port" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Next steps:" -ForegroundColor Yellow
    Write-Host "1. Configure AD credentials: $InstallPath\config\ad_credentials.json" -ForegroundColor White
    Write-Host "2. Test the web interface" -ForegroundColor White
    
} catch {
    Write-Log "Installation failed: $_" "ERROR"
    exit 1
}