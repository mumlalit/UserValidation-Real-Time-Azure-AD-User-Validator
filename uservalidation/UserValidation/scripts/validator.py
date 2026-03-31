"""Real-Time AD Validator"""
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import msal
import pandas as pd
import requests

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
SELECT_FIELDS = (
    "userPrincipalName,displayName,mail,accountEnabled,department,jobTitle,"
    "officeLocation,employeeId,companyName,mobilePhone"
)


class RealtimeValidator:
    def __init__(self, config):
        self.config = config
        self.db_path = Path(config.install_path) / "data" / "validation.db"
        self.token = None
        self.token_expires = None
        self._init_database()

    def _init_database(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS validation_history ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT NOT NULL,"
            "timestamp TEXT NOT NULL, total_users INTEGER, active_users INTEGER,"
            "terminated_users INTEGER, invalid_users INTEGER,"
            "duration_seconds REAL, result_json TEXT)"
        )
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS user_cache ("
            "email TEXT PRIMARY KEY, display_name TEXT, department TEXT,"
            "job_title TEXT, manager TEXT, account_enabled INTEGER,"
            "last_checked TEXT, raw_data TEXT)"
        )
        conn.commit()
        conn.close()
        logger.info("Database initialized")

    def get_access_token(self):
        try:
            if self.token and self.token_expires:
                if datetime.now().timestamp() < self.token_expires - 300:
                    return self.token

            cred_path = Path(self.config.install_path) / "config" / "ad_credentials.json"
            with open(cred_path, "r", encoding="utf-8") as f:
                creds = json.load(f)

            # Use the same client-secret flow as the working test script.
            tenant_id = creds.get("tenant_id")
            client_id = creds.get("client_id")
            client_secret = creds.get("client_secret")
            if not tenant_id or not client_id or not client_secret:
                raise KeyError("ad_credentials.json must contain tenant_id, client_id, and client_secret")

            authority = f"https://login.microsoftonline.com/{tenant_id}"
            app = msal.ConfidentialClientApplication(
                client_id,
                authority=authority,
                client_credential=client_secret,
            )
            result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])

            if "access_token" in result:
                self.token = result["access_token"]
                self.token_expires = datetime.now().timestamp() + int(result.get("expires_in", 3600))
                return self.token

            raise Exception("Token failed: " + str(result.get("error_description") or result))
        except Exception as e:
            logger.error("Error getting access token: " + str(e), exc_info=True)
            raise

    def _get_headers(self):
        token = self.get_access_token()
        return {
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        }

    def query_user(self, email):
        try:
            email = str(email).strip()
            headers = self._get_headers()

            # Primary lookup: exact UPN / object path, matching your test script.
            url = f"{GRAPH_BASE}/users/{email}"
            params = {"$select": SELECT_FIELDS}
            response = requests.get(url, headers=headers, params=params, timeout=30)

            # Fallback lookup: mail or userPrincipalName, in case the input is mail not UPN.
            if response.status_code == 404:
                url = f"{GRAPH_BASE}/users"
                params = {
                    "$filter": f"mail eq '{email}' or userPrincipalName eq '{email}'",
                    "$select": SELECT_FIELDS,
                    "$top": 1,
                }
                response = requests.get(url, headers=headers, params=params, timeout=30)

                if response.status_code == 200:
                    data = response.json().get("value", [])
                    if not data:
                        return {
                            "email": email,
                            "found": False,
                            "account_enabled": False,
                            "status": "Not Found in AD",
                        }
                    user_data = data[0]
                else:
                    return {
                        "email": email,
                        "found": False,
                        "account_enabled": False,
                        "status": "Query Failed",
                        "error": response.text,
                    }
            elif response.status_code == 200:
                user_data = response.json()
            else:
                return {
                    "email": email,
                    "found": False,
                    "account_enabled": False,
                    "status": "Query Failed",
                    "error": response.text,
                }

            manager_name = None
            try:
                user_upn = user_data.get("userPrincipalName") or email
                mr = requests.get(
                    f"{GRAPH_BASE}/users/{user_upn}/manager",
                    headers=headers,
                    timeout=30,
                )
                if mr.status_code == 200:
                    manager_name = mr.json().get("displayName")
            except Exception:
                pass

            self._cache_user(email, user_data, manager_name)
            account_enabled = bool(user_data.get("accountEnabled", False))
            return {
                "email": email,
                "found": True,
                "account_enabled": account_enabled,
                "display_name": user_data.get("displayName"),
                "department": user_data.get("department"),
                "job_title": user_data.get("jobTitle"),
                "office_location": user_data.get("officeLocation"),
                "manager": manager_name,
                "employee_id": user_data.get("employeeId"),
                "company": user_data.get("companyName"),
                "mobile": user_data.get("mobilePhone"),
                "status": "Active" if account_enabled else "Terminated",
            }
        except Exception as e:
            logger.error("Error querying user " + email + ": " + str(e), exc_info=True)
            return {
                "email": email,
                "found": False,
                "account_enabled": False,
                "status": "Error",
                "error": str(e),
            }

    def _cache_user(self, email, user_data, manager_name):
        try:
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO user_cache"
                " (email, display_name, department, job_title, manager, account_enabled, last_checked, raw_data)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    email,
                    user_data.get("displayName"),
                    user_data.get("department"),
                    user_data.get("jobTitle"),
                    manager_name,
                    1 if user_data.get("accountEnabled") else 0,
                    datetime.now().isoformat(),
                    json.dumps(user_data),
                ),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error("Error caching user: " + str(e), exc_info=True)

    def validate_file(self, filepath, progress_callback=None):
        start_time = datetime.now()
        try:
            logger.info("Validating file: " + str(filepath))
            file_ext = Path(filepath).suffix.lower()
            if file_ext in [".xlsx", ".xls"]:
                df = pd.read_excel(filepath)
            elif file_ext == ".csv":
                df = pd.read_csv(filepath)
            else:
                raise ValueError("Unsupported file type: " + file_ext)

            email_col = None
            for col in df.columns:
                if "email" in str(col).lower() or "mail" in str(col).lower():
                    email_col = col
                    break
            if email_col is None:
                raise ValueError("No email column found. Column must contain the word email or mail.")

            users = (
                df[email_col]
                .dropna()
                .astype(str)
                .str.strip()
                .unique()
            )
            total_users = len(users)
            logger.info("Found " + str(total_users) + " unique users")

            results = []
            active_users = []
            terminated_users = []
            invalid_users = []

            with ThreadPoolExecutor(max_workers=self.config.max_concurrent_queries) as executor:
                future_to_email = {executor.submit(self.query_user, email): email for email in users}
                for idx, future in enumerate(as_completed(future_to_email), 1):
                    email = future_to_email[future]
                    try:
                        user_data = future.result()
                        results.append(user_data)
                        if not user_data["found"]:
                            invalid_users.append(user_data)
                        elif user_data["account_enabled"]:
                            active_users.append(user_data)
                        else:
                            terminated_users.append(user_data)

                        if progress_callback:
                            status = "active" if user_data.get("account_enabled") else "terminated"
                            if not user_data["found"]:
                                status = "not_found"
                            progress_callback(idx, total_users, email, status)
                    except Exception as e:
                        logger.error("Error processing " + email + ": " + str(e))
                        invalid_users.append({"email": email, "found": False, "status": "Error", "error": str(e)})

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
                "has_issues": len(terminated_users) > 0 or len(invalid_users) > 0,
            }
            self._log_validation(result)
            return result
        except Exception as e:
            logger.error("Validation failed: " + str(e), exc_info=True)
            return {"success": False, "error": str(e), "timestamp": datetime.now().isoformat()}

    def generate_report(self, result, filename):
        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            report_dir = Path(self.config.install_path) / "reports"
            report_dir.mkdir(parents=True, exist_ok=True)
            report_path = report_dir / (ts + "_" + Path(filename).stem + ".html")

            def make_table(users, color, title):
                if not users:
                    return ""
                rows = ""
                for u in users:
                    rows += (
                        "<tr><td>" + str(u.get("email", "-")) + "</td><td>" + str(u.get("display_name", "-")) +
                        "</td><td>" + str(u.get("department", "-")) + "</td><td>" + str(u.get("job_title", "-")) +
                        "</td><td>" + str(u.get("manager", "-")) + "</td><td>" + str(u.get("office_location", "-")) +
                        "</td><td>" + str(u.get("status", "-")) + "</td></tr>"
                    )
                return (
                    "<h2 style='color:" + color + "'>" + title + " (" + str(len(users)) + ")</h2>"
                    "<table border=\"1\" cellpadding=\"8\" width=\"100%\"><tr><th>Email</th><th>Name</th>"
                    "<th>Department</th><th>Job Title</th><th>Manager</th><th>Office</th><th>Status</th></tr>"
                    + rows + "</table><br>"
                )

            html = (
                "<!DOCTYPE html><html><head><meta charset=\"UTF-8\"><title>Validation Report</title>"
                "<style>body{font-family:Segoe UI,sans-serif;padding:20px}table{border-collapse:collapse;width:100%}"
                "th{background:#f0f0f0;padding:10px}td{padding:8px}tr:nth-child(even){background:#f9f9f9}</style></head><body>"
                "<h1>User Validation Report</h1><p><b>File:</b> " + result["filename"] +
                " | <b>Generated:</b> " + datetime.now().strftime("%Y-%m-%d %H:%M") +
                " | <b>Duration:</b> " + str(round(result.get("duration_seconds", 0), 2)) + "s</p>"
                "<p>Total: " + str(result["total_users"]) + " | Active: " + str(result["active_users"]) +
                " | Terminated: " + str(result["terminated_users"]) + " | Not Found: " + str(result["invalid_users"]) + "</p>"
                + make_table(result.get("terminated_list", []), "#dc3545", "Terminated Users")
                + make_table(result.get("invalid_list", []), "#e6a817", "Not Found in AD")
                + make_table(result.get("active_list", []), "#28a745", "Active Users")
                + "</body></html>"
            )
            with open(report_path, "w", encoding="utf-8") as f:
                f.write(html)
            logger.info("Report generated: " + str(report_path))
            return str(report_path)
        except Exception as e:
            logger.error("Report generation failed: " + str(e), exc_info=True)
            raise

    def _log_validation(self, result):
        try:
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO validation_history"
                " (filename, timestamp, total_users, active_users, terminated_users,"
                " invalid_users, duration_seconds, result_json) VALUES (?,?,?,?,?,?,?,?)",
                (
                    result["filename"], result["timestamp"], result["total_users"],
                    result["active_users"], result["terminated_users"], result["invalid_users"],
                    result.get("duration_seconds"), json.dumps(result),
                ),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error("Error logging validation: " + str(e), exc_info=True)

    def test_ad_connection(self):
        try:
            return self.get_access_token() is not None
        except Exception:
            return False

    def get_stats(self):
        try:
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM validation_history")
            validations = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM user_cache")
            cached_users = cursor.fetchone()[0]
            conn.close()
            return {"validations": validations, "cached_users": cached_users}
        except Exception as e:
            logger.error("Error getting stats: " + str(e), exc_info=True)
            return {"error": str(e)}
