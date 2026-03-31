# ================================================================
# Quick Fix Deployment Script
# Replaces validator.py and restarts the service
# ================================================================

Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  User Validation - Quick Fix Deployment" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""

$InstallPath = "C:\UserValidation"
$BackupPath = "$InstallPath\app\validator.py.backup_$(Get-Date -Format 'yyyyMMdd_HHmmss')"

try {
    # 1. Backup current validator.py
    Write-Host "[1] Creating backup..." -ForegroundColor Yellow
    if (Test-Path "$InstallPath\app\validator.py") {
        Copy-Item "$InstallPath\app\validator.py" -Destination $BackupPath
        Write-Host "    Backup created: $BackupPath" -ForegroundColor Green
    }
    
    # 2. Stop service
    Write-Host "[2] Stopping service..." -ForegroundColor Yellow
    Stop-Service -Name "UserValidationWeb" -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    Write-Host "    Service stopped" -ForegroundColor Green
    
    # 3. Deploy new validator.py
    Write-Host "[3] Deploying fixed validator.py..." -ForegroundColor Yellow
    # User should copy the new validator.py to this location first
    if (Test-Path "$PSScriptRoot\validator.py") {
        Copy-Item "$PSScriptRoot\validator.py" -Destination "$InstallPath\app\validator.py" -Force
        Write-Host "    New validator.py deployed" -ForegroundColor Green
    } else {
        throw "validator.py not found in script directory!"
    }
    
    # 4. Verify credentials file
    Write-Host "[4] Verifying credentials..." -ForegroundColor Yellow
    $credPath = "$InstallPath\config\ad_credentials.json"
    if (Test-Path $credPath) {
        $creds = Get-Content $credPath | ConvertFrom-Json
        if ($creds.tenant_id -and $creds.client_id -and $creds.client_secret) {
            Write-Host "    Credentials verified" -ForegroundColor Green
        } else {
            throw "Missing required fields in ad_credentials.json"
        }
    } else {
        throw "Credentials file not found!"
    }
    
    # 5. Start service
    Write-Host "[5] Starting service..." -ForegroundColor Yellow
    Start-Service -Name "UserValidationWeb"
    Start-Sleep -Seconds 5
    Write-Host "    Service started" -ForegroundColor Green
    
    # 6. Test service
    Write-Host "[6] Testing service..." -ForegroundColor Yellow
    try {
        $response = Invoke-WebRequest -Uri "http://10.228.176.17:8080/health" -TimeoutSec 10
        $health = $response.Content | ConvertFrom-Json
        
        if ($health.status -eq "healthy" -and $health.ad_connection -eq $true) {
            Write-Host "    ✓ Service is healthy" -ForegroundColor Green
            Write-Host "    ✓ AD connection is working" -ForegroundColor Green
        } else {
            Write-Host "    ⚠ Service started but may have issues" -ForegroundColor Yellow
            Write-Host "    Health response: $($response.Content)" -ForegroundColor Yellow
        }
    } catch {
        Write-Host "    ⚠ Could not verify health endpoint" -ForegroundColor Yellow
    }
    
    Write-Host ""
    Write-Host "================================================" -ForegroundColor Green
    Write-Host "  Deployment Complete!" -ForegroundColor Green
    Write-Host "================================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "Next steps:" -ForegroundColor Cyan
    Write-Host "1. Open browser: http://10.228.176.17:8080" -ForegroundColor White
    Write-Host "2. Upload a test Excel file" -ForegroundColor White
    Write-Host "3. Verify validation works correctly" -ForegroundColor White
    Write-Host ""
    Write-Host "If issues persist, check logs:" -ForegroundColor Yellow
    Write-Host "  $InstallPath\logs\app.log" -ForegroundColor White
    Write-Host "  $InstallPath\logs\error.log" -ForegroundColor White
    
} catch {
    Write-Host ""
    Write-Host "================================================" -ForegroundColor Red
    Write-Host "  Deployment Failed!" -ForegroundColor Red
    Write-Host "================================================" -ForegroundColor Red
    Write-Host ""
    Write-Host "Error: $_" -ForegroundColor Red
    Write-Host ""
    
    # Try to restore backup if it exists
    if (Test-Path $BackupPath) {
        Write-Host "Restoring from backup..." -ForegroundColor Yellow
        Copy-Item $BackupPath -Destination "$InstallPath\app\validator.py" -Force
        Start-Service -Name "UserValidationWeb" -ErrorAction SilentlyContinue
        Write-Host "Backup restored and service restarted" -ForegroundColor Yellow
    }
    
    exit 1
}
