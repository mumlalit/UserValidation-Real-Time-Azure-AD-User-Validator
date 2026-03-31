"""
Real-Time AD Validator - FIXED VERSION
Queries Active Directory on-demand with client_secret authentication
"""

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import msal
import requests

logger = logging.getLogger(__name__)


class RealtimeValidator:
    def __init__(self, config):
        self.config = config
        self.db_path = Path(config.install_path) / "data" / "validation.db"
        self.token = None
        self.token_expires = None
        self._init_database()

    def _init_database(self):
        """Initialize SQLite database"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS validation_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                total_users INTEGER,
                active_users INTEGER,
                terminated_users INTEGER,
                invalid_users INTEGER,
                duration_seconds REAL,
                result_json TEXT
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_cache (
                email TEXT PRIMARY KEY,
                display_name TEXT,
                department TEXT,
                job_title TEXT,
                manager TEXT,
                account_enabled INTEGER,
                last_checked TEXT,
                raw_data TEXT
            )
        ''')
        
        conn.commit()
        conn.close()
        logger.info("Database initialized")

    def get_access_token(self):
        """Get access token using client_secret"""
        try:
            # Check if existing token is still valid
            if self.token and self.token_expires:
                if datetime.now().timestamp() < self.token_expires - 300:  # 5 min buffer
                    logger.debug("Using cached token")
                    return self.token
            
            # Load credentials
            cred_path = Path(self.config.install_path) / "config" / "ad_credentials.json"
            
            if not cred_path.exists():
                raise Exception(f"Credentials file not found: {cred_path}")
            
            with open(cred_path, "r") as f:
                creds = json.load(f)
            
            # Validate required fields
            required_fields = ["tenant_id", "client_id", "client_secret"]
            missing = [f for f in required_fields if f not in creds]
            if missing:
                raise Exception(f"Missing required fields in credentials: {missing}")
            
            logger.info("Acquiring new access token...")
            
            # Create MSAL app with client_secret
            authority = f"https://login.microsoftonline.com/{creds['tenant_id']}"
            app = msal.ConfidentialClientApplication(
                creds["client_id"],
                authority=authority,
                client_credential=creds["client_secret"]
            )
            
            # Get token
            result = app.acquire_token_for_client(
                scopes=["https://graph.microsoft.com/.default"]
            )
            
            if "access_token" in result:
                self.token = result["access_token"]
                self.token_expires = datetime.now().timestamp() + result["expires_in"]
                logger.info("✓ Access token acquired successfully")
                return self.token
            else:
                error_msg = result.get("error_description", result.get("error", "Unknown error"))
                raise Exception(f"Token acquisition failed: {error_msg}")
                
        except FileNotFoundError as e:
            logger.error(f"Credentials file not found: {e}")
            raise Exception("AD credentials file not found. Please configure credentials.")
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in credentials file: {e}")
            raise Exception("Invalid credentials file format")
        except Exception as e:
            logger.error(f"Error getting access token: {e}", exc_info=True)
            raise

    def query_user(self, email):
        """Query single user from Azure AD"""
        try:
            token = self.get_access_token()
            
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            }
            
            # Query user with all fields
            url = f"https://graph.microsoft.com/v1.0/users/{email}"
            params = {
                "$select": "userPrincipalName,displayName,mail,accountEnabled,department,jobTitle,officeLocation,employeeId,companyName,mobilePhone"
            }
            
            response = requests.get(url, headers=headers, params=params, timeout=30)
            
            if response.status_code == 200:
                user_data = response.json()
                
                # Get manager info
                manager_name = None
                try:
                    manager_url = f"https://graph.microsoft.com/v1.0/users/{email}/manager"
                    manager_response = requests.get(manager_url, headers=headers, timeout=30)
                    if manager_response.status_code == 200:
                        manager_data = manager_response.json()
                        manager_name = manager_data.get("displayName")
                except Exception as e:
                    logger.debug(f"Could not fetch manager for {email}: {e}")
                
                # Cache the result
                self._cache_user(email, user_data, manager_name)
                
                return {
                    "email": email,
                    "found": True,
                    "account_enabled": user_data.get("accountEnabled", False),
                    "display_name": user_data.get("displayName"),
                    "department": user_data.get("department"),
                    "job_title": user_data.get("jobTitle"),
                    "office_location": user_data.get("officeLocation"),
                    "manager": manager_name,
                    "employee_id": user_data.get("employeeId"),
                    "company": user_data.get("companyName"),
                    "mobile": user_data.get("mobilePhone"),
                    "status": "Active" if user_data.get("accountEnabled") else "Terminated"
                }
            
            elif response.status_code == 404:
                logger.debug(f"User not found: {email}")
                return {
                    "email": email,
                    "found": False,
                    "account_enabled": False,
                    "status": "Not Found in AD"
                }
            
            else:
                logger.warning(f"AD query failed for {email}: {response.status_code} - {response.text}")
                return {
                    "email": email,
                    "found": False,
                    "account_enabled": False,
                    "status": "Query Failed",
                    "error": f"HTTP {response.status_code}"
                }
                
        except requests.exceptions.Timeout:
            logger.error(f"Timeout querying user {email}")
            return {
                "email": email,
                "found": False,
                "account_enabled": False,
                "status": "Timeout",
                "error": "Request timed out"
            }
        except Exception as e:
            logger.error(f"Error querying user {email}: {e}", exc_info=True)
            return {
                "email": email,
                "found": False,
                "account_enabled": False,
                "status": "Error",
                "error": str(e)
            }

    def _cache_user(self, email, user_data, manager_name):
        """Cache user data in database"""
        try:
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT OR REPLACE INTO user_cache
                (email, display_name, department, job_title, manager, account_enabled, last_checked, raw_data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                email,
                user_data.get("displayName"),
                user_data.get("department"),
                user_data.get("jobTitle"),
                manager_name,
                1 if user_data.get("accountEnabled") else 0,
                datetime.now().isoformat(),
                json.dumps(user_data)
            ))
            
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Error caching user: {e}", exc_info=True)

    def validate_file(self, filepath, progress_callback=None):
        """Validate Excel file with real-time AD queries"""
        start_time = datetime.now()
        
        try:
            logger.info(f"Starting validation: {filepath}")
            
            # Read file
            file_ext = Path(filepath).suffix.lower()
            if file_ext in [".xlsx", ".xls"]:
                df = pd.read_excel(filepath)
            elif file_ext == ".csv":
                df = pd.read_csv(filepath)
            else:
                raise ValueError(f"Unsupported file type: {file_ext}")
            
            # Find email column
            email_col = None
            for col in df.columns:
                if "email" in str(col).lower() or "mail" in str(col).lower():
                    email_col = col
                    break
            
            if email_col is None:
                raise ValueError("No email column found in file. Please ensure there's a column with 'email' in the name.")
            
            # Extract unique emails
            users = df[email_col].dropna().astype(str).str.strip().str.lower().unique()
            total_users = len(users)
            
            logger.info(f"Found {total_users} unique users to validate")
            
            # Validate users with concurrent queries
            results = []
            active_users = []
            terminated_users = []
            invalid_users = []
            
            with ThreadPoolExecutor(max_workers=self.config.max_concurrent_queries) as executor:
                # Submit all user queries
                future_to_email = {
                    executor.submit(self.query_user, email): email 
                    for email in users
                }
                
                # Process results as they complete
                for idx, future in enumerate(as_completed(future_to_email), 1):
                    email = future_to_email[future]
                    
                    try:
                        user_data = future.result()
                        results.append(user_data)
                        
                        # Categorize
                        if not user_data["found"]:
                            invalid_users.append(user_data)
                        elif user_data["account_enabled"]:
                            active_users.append(user_data)
                        else:
                            terminated_users.append(user_data)
                        
                        # Send progress update
                        if progress_callback:
                            status = "active" if user_data.get("account_enabled") else "terminated"
                            if not user_data["found"]:
                                status = "not_found"
                            progress_callback(idx, total_users, email, status)
                        
                        logger.debug(f"Processed {idx}/{total_users}: {email} -> {user_data['status']}")
                        
                    except Exception as e:
                        logger.error(f"Error processing {email}: {e}")
                        invalid_users.append({
                            "email": email,
                            "found": False,
                            "status": "Error",
                            "error": str(e)
                        })
            
            duration = (datetime.now() - start_time).total_seconds()
            
            result = {
                "success": True,
                "timestamp": datetime.now().isoformat(),
                "filename": Path(filepath).name,
                "total_users": total_users,
                "active_users": len(active_users),
                "terminated_users": len(terminated_users),
                "invalid_users": len(invalid_users),
                "duration_seconds": duration,
                "active_list": sorted(active_users, key=lambda x: x["email"]),
                "terminated_list": sorted(terminated_users, key=lambda x: x["email"]),
                "invalid_list": sorted(invalid_users, key=lambda x: x["email"]),
                "has_issues": len(terminated_users) > 0 or len(invalid_users) > 0
            }
            
            # Log to database
            self._log_validation(result)
            
            logger.info(f"✓ Validation complete: {len(active_users)} active, {len(terminated_users)} terminated, {len(invalid_users)} not found")
            logger.info(f"✓ Duration: {duration:.2f} seconds ({duration/total_users:.2f}s per user)")
            
            return result
            
        except Exception as e:
            logger.error(f"Validation failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }

    def generate_report(self, result, filename):
        """Generate enhanced HTML report"""
        try:
            report_path = Path(self.config.install_path) / "reports" / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{Path(filename).stem}.html"
            
            # Generate user tables
            def generate_user_table(users, status_class, title):
                if not users:
                    return ""
                
                html = f"""
                <div class="user-section">
                    <h2 class="{status_class}">{title} ({len(users)})</h2>
                    <table class="user-table">
                        <thead>
                            <tr>
                                <th>Email</th>
                                <th>Name</th>
                                <th>Department</th>
                                <th>Job Title</th>
                                <th>Manager</th>
                                <th>Office</th>
                                <th>Employee ID</th>
                                <th>Status</th>
                            </tr>
                        </thead>
                        <tbody>
                """
                
                for user in users:
                    html += f"""
                        <tr>
                            <td>{user.get('email', '-')}</td>
                            <td>{user.get('display_name', '-')}</td>
                            <td>{user.get('department', '-')}</td>
                            <td>{user.get('job_title', '-')}</td>
                            <td>{user.get('manager', '-')}</td>
                            <td>{user.get('office_location', '-')}</td>
                            <td>{user.get('employee_id', '-')}</td>
                            <td><span class="status-badge {status_class}">{user.get('status', '-')}</span></td>
                        </tr>
                    """
                
                html += """
                        </tbody>
                    </table>
                </div>
                """
                return html
            
            html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>User Validation Report</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: #f5f5f5;
            padding: 20px;
        }}
        .container {{
            max-width: 1400px;
            margin: 0 auto;
            background: white;
            border-radius: 10px;
            box-shadow: 0 2px 20px rgba(0,0,0,0.1);
            overflow: hidden;
        }}
        .header {{
            background: linear-gradient(135deg, #0066cc 0%, #004999 100%);
            color: white;
            padding: 40px;
        }}
        .header h1 {{ font-size: 2.5em; margin-bottom: 10px; }}
        .header-info {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin-top: 30px;
        }}
        .header-info-item {{
            background: rgba(255,255,255,0.1);
            padding: 15px;
            border-radius: 5px;
        }}
        .header-info-item label {{
            display: block;
            opacity: 0.8;
            font-size: 0.9em;
            margin-bottom: 5px;
        }}
        .header-info-item value {{
            font-size: 1.3em;
            font-weight: bold;
        }}
        .summary {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            padding: 40px;
            background: #f8f9fa;
        }}
        .summary-card {{
            background: white;
            padding: 25px;
            border-radius: 8px;
            text-align: center;
            box-shadow: 0 2px 10px rgba(0,0,0,0.05);
        }}
        .summary-card h3 {{
            color: #666;
            font-size: 0.9em;
            margin-bottom: 15px;
            text-transform: uppercase;
        }}
        .summary-card .value {{
            font-size: 2.5em;
            font-weight: bold;
        }}
        .summary-card.total .value {{ color: #333; }}
        .summary-card.success .value {{ color: #28a745; }}
        .summary-card.error .value {{ color: #dc3545; }}
        .summary-card.warning .value {{ color: #ffc107; }}
        .content {{ padding: 40px; }}
        .user-section {{ margin-bottom: 40px; }}
        .user-section h2 {{
            margin-bottom: 20px;
            padding-bottom: 10px;
            border-bottom: 3px solid #eee;
        }}
        .user-section h2.success {{ border-color: #28a745; color: #28a745; }}
        .user-section h2.error {{ border-color: #dc3545; color: #dc3545; }}
        .user-section h2.warning {{ border-color: #ffc107; color: #ffc107; }}
        .user-table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 0.9em;
        }}
        .user-table thead {{
            background: #f8f9fa;
        }}
        .user-table th {{
            padding: 12px;
            text-align: left;
            font-weight: 600;
            color: #666;
            border-bottom: 2px solid #dee2e6;
        }}
        .user-table td {{
            padding: 12px;
            border-bottom: 1px solid #dee2e6;
        }}
        .user-table tbody tr:hover {{
            background: #f8f9fa;
        }}
        .status-badge {{
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 0.85em;
            font-weight: 600;
        }}
        .status-badge.success {{
            background: #d4edda;
            color: #155724;
        }}
        .status-badge.error {{
            background: #f8d7da;
            color: #721c24;
        }}
        .status-badge.warning {{
            background: #fff3cd;
            color: #856404;
        }}
        .footer {{
            background: #f8f9fa;
            padding: 20px 40px;
            text-align: center;
            color: #666;
            font-size: 0.9em;
            border-top: 1px solid #dee2e6;
        }}
        @media print {{
            body {{ background: white; padding: 0; }}
            .container {{ box-shadow: none; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📊 User Validation Report</h1>
            <div class="header-info">
                <div class="header-info-item">
                    <label>Report Generated</label>
                    <value>{datetime.now().strftime('%B %d, %Y at %I:%M %p')}</value>
                </div>
                <div class="header-info-item">
                    <label>Source File</label>
                    <value>{result['filename']}</value>
                </div>
                <div class="header-info-item">
                    <label>Processing Time</label>
                    <value>{result.get('duration_seconds', 0):.2f} seconds</value>
                </div>
                <div class="header-info-item">
                    <label>Validation Status</label>
                    <value>{'⚠️ Issues Found' if result['has_issues'] else '✅ All Clear'}</value>
                </div>
            </div>
        </div>
        
        <div class="summary">
            <div class="summary-card total">
                <h3>Total Users</h3>
                <div class="value">{result['total_users']}</div>
            </div>
            <div class="summary-card success">
                <h3>Active</h3>
                <div class="value">{result['active_users']}</div>
            </div>
            <div class="summary-card error">
                <h3>Terminated</h3>
                <div class="value">{result['terminated_users']}</div>
            </div>
            <div class="summary-card warning">
                <h3>Not Found</h3>
                <div class="value">{result['invalid_users']}</div>
            </div>
        </div>
        
        <div class="content">
            {generate_user_table(result.get('terminated_list', []), 'error', '❌ Terminated Users')}
            {generate_user_table(result.get('invalid_list', []), 'warning', '⚠️ Users Not Found in AD')}
            {generate_user_table(result.get('active_list', []), 'success', '✅ Active Users')}
        </div>
        
        <div class="footer">
            <p>Generated by User Validation System | Real-Time AD Validation</p>
            <p style="margin-top: 5px; font-size: 0.85em;">This report contains current data from Active Directory as of {datetime.now().isoformat()}</p>
        </div>
    </div>
</body>
</html>
            """
            
            with open(report_path, "w", encoding="utf-8") as f:
                f.write(html)
            
            logger.info(f"Report generated: {report_path}")
            return str(report_path)
            
        except Exception as e:
            logger.error(f"Report generation failed: {e}", exc_info=True)
            raise

    def _log_validation(self, result):
        """Log validation to database"""
        try:
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO validation_history
                (filename, timestamp, total_users, active_users, terminated_users, invalid_users, duration_seconds, result_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                result["filename"],
                result["timestamp"],
                result["total_users"],
                result["active_users"],
                result["terminated_users"],
                result["invalid_users"],
                result.get("duration_seconds"),
                json.dumps(result)
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Error logging validation: {e}", exc_info=True)

    def test_ad_connection(self):
        """Test Active Directory connection"""
        try:
            token = self.get_access_token()
            return token is not None
        except:
            return False

    def get_stats(self):
        """Get validation statistics"""
        try:
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()
            
            cursor.execute("SELECT COUNT(*) FROM validation_history")
            total = cursor.fetchone()[0]
            
            cursor.execute("SELECT SUM(total_users) FROM validation_history")
            total_processed = cursor.fetchone()[0] or 0
            
            cursor.execute("SELECT AVG(duration_seconds) FROM validation_history WHERE duration_seconds IS NOT NULL")
            avg_duration = cursor.fetchone()[0] or 0
            
            conn.close()
            
            return {
                "total_validations": total,
                "total_users_processed": total_processed,
                "average_duration": round(avg_duration, 2),
                "service_type": "realtime"
            }
        except Exception as e:
            logger.error(f"Error getting stats: {e}", exc_info=True)
            return {}
