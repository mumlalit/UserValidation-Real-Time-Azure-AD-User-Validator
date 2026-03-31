# UserValidation — Real-Time Azure AD User Validator

A lightweight Windows service that validates a list of email addresses against **Azure Active Directory (Microsoft Graph API)** in real time. Upload an Excel or CSV file through the web UI, watch live progress, and download a full report showing which users are active, terminated, or not found.

---

## ✨ Features

- **Real-time validation** — live progress updates via WebSockets (no page refresh needed)
- **Bulk processing** — upload `.xlsx`, `.xls`, or `.csv` files with hundreds or thousands of emails
- **Azure AD integration** — queries Microsoft Graph API directly using a service principal (client credentials flow)
- **Detailed reports** — downloadable Excel report with per-user status: `Active`, `Terminated`, or `Not Found`
- **SQLite history** — keeps a local database of every validation run and a per-user cache to speed up repeat lookups
- **Windows Service** — runs silently in the background via NSSM; survives reboots automatically
- **Health endpoint** — `/health` route you can hook into monitoring tools

---

## 🏗️ Architecture

```
UserValidation/
├── app/
│   ├── main.py          # Flask + Flask-SocketIO web server (Waitress)
│   ├── validator.py     # Core validation logic — MS Graph queries, report generation
│   ├── ad_sync.py       # (Optional) Background AD cache sync service
│   ├── config.py        # Loads settings from config/app_config.json
│   └── templates/
│       └── index.html   # Single-page web UI
├── config/
│   ├── app_config.json  # Server settings (IP, port, paths, etc.)
│   └── ad_credentials.json  # Azure App Registration credentials ← keep secret
├── data/
│   └── validation.db    # SQLite database (auto-created)
├── logs/                # Rotating log files
├── reports/             # Generated Excel reports
├── scripts/
│   ├── install.ps1      # One-shot install script (run as Administrator)
│   ├── setup-service.ps1# Registers the app as a Windows service via NSSM
│   ├── deploy-fix.ps1   # Troubleshooting / redeployment helper
│   └── health-check.ps1 # Scheduled health check script
└── requirements.txt
```

---

## 📋 Prerequisites

| Requirement | Notes |
|---|---|
| Windows Server 2016+ (or Windows 10+) | The service layer uses NSSM and PowerShell |
| Python 3.11+ | Must be on the system PATH |
| Azure App Registration | Needs `User.Read.All` (Application permission) in Microsoft Graph |
| Admin rights | Required for the installer and service registration |

---

## ⚙️ Azure App Registration Setup

Before installing, you need an **Azure AD App Registration** with the right permissions.

1. Go to **Azure Portal → Azure Active Directory → App registrations → New registration**
2. Give it a name (e.g. `UserValidation-Service`) and click **Register**
3. Note down:
   - **Application (client) ID**
   - **Directory (tenant) ID**
4. Go to **Certificates & secrets → New client secret** — copy the value immediately
5. Go to **API permissions → Add a permission → Microsoft Graph → Application permissions**
   - Add `User.Read.All`
   - Click **Grant admin consent**

Fill in `config/ad_credentials.json` with these values (see [Configuration](#configuration)).

---

## 🚀 Installation

### 1. Clone the repository

```powershell
git clone https://github.com/YOUR_USERNAME/UserValidation.git
cd UserValidation
```

### 2. Edit configuration files

**`config/ad_credentials.json`**
```json
{
  "tenant_id": "YOUR_TENANT_ID",
  "client_id": "YOUR_CLIENT_ID",
  "client_secret": "YOUR_CLIENT_SECRET"
}
```

**`config/app_config.json`**
```json
{
  "log_retention_days": 90,
  "admin_email": "admin@yourdomain.com",
  "server_ip": "YOUR_SERVER_IP",
  "install_path": "C:\\UserValidation",
  "max_concurrent_queries": 5,
  "max_upload_size_mb": 50,
  "port": 8080,
  "batch_size": 10
}
```

> ⚠️ **Never commit `ad_credentials.json` to version control.** Add it to `.gitignore`.

### 3. Run the installer (as Administrator)

```powershell
Set-ExecutionPolicy RemoteSigned -Scope Process
.\scripts\install.ps1 -ServerIP "YOUR_SERVER_IP" -InstallPath "C:\UserValidation"
```

The installer will:
- Create the directory structure under `C:\UserValidation`
- Copy all files
- Create a Python virtual environment and install dependencies
- Register the app as a Windows service using NSSM

### 4. Start the service

```powershell
Start-Service UserValidation
```

Then open your browser and navigate to:
```
http://YOUR_SERVER_IP:8080
```

---

## 🖥️ Usage

1. **Open the web UI** at `http://YOUR_SERVER_IP:8080`
2. **Upload a file** — Excel (`.xlsx` / `.xls`) or CSV with a column containing email addresses
3. **Watch live progress** — a progress bar updates in real time as each user is queried
4. **Download the report** — when complete, a button appears to download the full Excel report

### Input file format

The file needs at least one column with email addresses. Common column headers like `Email`, `User Email`, `UPN`, or `userPrincipalName` are all detected automatically.

| Name | Email |
|---|---|
| Jane Smith | jane.smith@company.com |
| John Doe | john.doe@company.com |

### Output report columns

| Column | Description |
|---|---|
| Email | The input email address |
| Display Name | Full name from Azure AD |
| Status | `Active`, `Terminated`, or `Not Found` |
| Department | Department from Azure AD |
| Job Title | Job title from Azure AD |
| Account Enabled | `True` / `False` |
| Last Checked | Timestamp of the query |

---

## 🔌 API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/` | Web UI |
| `POST` | `/upload` | Upload a file for validation |
| `GET` | `/download/<filename>` | Download a generated report |
| `GET` | `/health` | Health check (returns JSON) |
| `GET` | `/stats` | Validation statistics |

WebSocket events (via Socket.IO):

| Event | Direction | Payload |
|---|---|---|
| `validate_file` | Client → Server | `{ filepath, filename }` |
| `validation_progress` | Server → Client | `{ current, total, percentage, user, status }` |
| `validation_complete` | Server → Client | Full result object |
| `validation_error` | Server → Client | `{ error }` |

---

## 🛠️ Service Management

```powershell
# Start / stop / restart
Start-Service UserValidation
Stop-Service UserValidation
Restart-Service UserValidation

# Check status
Get-Service UserValidation

# View logs
Get-Content C:\UserValidation\logs\app.log -Tail 50 -Wait

# Run health check manually
.\scripts\health-check.ps1
```

---

## 🔧 Troubleshooting

**Service won't start**
- Check `logs\app.log` and `logs\error.log`
- Verify Python is on the system PATH: `python --version`
- Verify the virtual environment was created: `C:\UserValidation\venv\Scripts\python.exe`

**"Token acquisition failed" error**
- Double-check `tenant_id`, `client_id`, and `client_secret` in `ad_credentials.json`
- Make sure admin consent was granted for `User.Read.All` in Azure Portal

**Users showing as "Not Found" when they should be active**
- Confirm the email format matches the Azure AD `userPrincipalName` (UPN)
- Test the credential by hitting `/health` — it reports AD connection status

**Port already in use**
- Change `port` in `config/app_config.json` and restart the service

---

## 📦 Dependencies

```
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
```

---

## 🔒 Security Notes

- `ad_credentials.json` contains your Azure client secret — **do not commit it**. Add it to `.gitignore` and manage it through your deployment pipeline or secrets manager.
- The web UI has no built-in authentication. If exposing beyond localhost, put it behind a reverse proxy (IIS, nginx) with Windows Auth or basic auth.
- Uploaded files are automatically deleted after validation completes.

---

## 📄 License

MIT — see [LICENSE](LICENSE) for details.
