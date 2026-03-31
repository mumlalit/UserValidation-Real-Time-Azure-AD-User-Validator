<#
.SYNOPSIS
    Health monitoring and auto-recovery for real-time service
#>

$InstallPath = "C:\UserValidation"
$LogPath = "$InstallPath\logs\health.log"

function Write-HealthLog {
    param([string]$Message)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$timestamp - $Message" | Out-File $LogPath -Append
}

try {
    Write-HealthLog "=== Health Check Started ==="
    
    # Check Web Service
    $service = Get-Service -Name "UserValidationWeb" -ErrorAction SilentlyContinue
    if ($service.Status -ne "Running") {
        Write-HealthLog "WARNING: Service is $($service.Status). Restarting..."
        Start-Service -Name "UserValidationWeb"
        Start-Sleep -Seconds 5
        
        if ((Get-Service -Name "UserValidationWeb").Status -eq "Running") {
            Write-HealthLog "SUCCESS: Service restarted"
        } else {
            Write-HealthLog "ERROR: Failed to restart service"
        }
    }
    
    # Check web endpoint
    try {
        $response = Invoke-WebRequest -Uri "http://10.228.176.17:8080/health" -TimeoutSec 10
        Write-HealthLog "Web endpoint healthy"
    } catch {
        Write-HealthLog "ERROR: Web endpoint check failed: $_"
    }
    
    # Cleanup old logs (90 days)
    Get-ChildItem "$InstallPath\logs\*.log" | Where-Object {
        $_.LastWriteTime -lt (Get-Date).AddDays(-90)
    } | Remove-Item -Force
    
    # Cleanup old reports (30 days)
    Get-ChildItem "$InstallPath\reports\*.html" | Where-Object {
        $_.LastWriteTime -lt (Get-Date).AddDays(-30)
    } | Remove-Item -Force
    
    Write-HealthLog "=== Health Check Completed ==="
    
} catch {
    Write-HealthLog "CRITICAL ERROR: $_"
}