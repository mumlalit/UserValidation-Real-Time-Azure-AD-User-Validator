#!/usr/bin/env python3
"""
Active Directory Sync Service
Syncs active users from AD/Azure AD to local cache
Runs as background Windows service
"""

import json
import logging
import time
from datetime import datetime
from pathlib import Path
import msal
import requests
from apscheduler.schedulers.blocking import BlockingScheduler
from config import Config

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('C:\\UserValidation\\logs\\sync.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class ADSync:
    def __init__(self, config):
        self.config = config
        self.cache_path = Path(config.install_path) / 'data' / 'cache' / 'active_users.json'
        self.token = None
        self.token_expires = None
    
    def get_access_token(self):
        """Get access token using MSAL with certificate"""
        try:
            # Load AD credentials
            cred_path = Path(self.config.install_path) / 'config' / 'ad_credentials.json'
            with open(cred_path, 'r') as f:
                creds = json.load(f)
            
            # Create MSAL app with certificate
            app = msal.ConfidentialClientApplication(
                creds['client_id'],
                authority=f"https://login.microsoftonline.com/{creds['tenant_id']}",
                client_credential={
                    "thumbprint": creds['cert_thumbprint'],
                    "private_key": open(creds['cert_path'], "r").read()
                }
            )
            
            # Get token
            result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
            
            if "access_token" in result:
                self.token = result['access_token']
                self.token_expires = datetime.now().timestamp() + result['expires_in']
                logger.info("Access token acquired successfully")
                return self.token
            else:
                raise Exception(f"Token acquisition failed: {result.get('error_description')}")
                
        except Exception as e:
            logger.error(f"Error getting access token: {e}", exc_info=True)
            raise
    
    def sync_users(self):
        """Sync active users from Azure AD/Microsoft Graph"""
        try:
            logger.info("Starting user sync...")
            
            # Get fresh token if needed
            if not self.token or datetime.now().timestamp() >= self.token_expires:
                self.get_access_token()
            
            # Query Microsoft Graph for active users
            headers = {
                'Authorization': f'Bearer {self.token}',
                'Content-Type': 'application/json'
            }
            
            active_users = []
            next_link = 'https://graph.microsoft.com/v1.0/users?$select=userPrincipalName,accountEnabled&$filter=accountEnabled eq true'
            
            while next_link:
                response = requests.get(next_link, headers=headers)
                response.raise_for_status()
                data = response.json()
                
                # Extract active users
                for user in data.get('value', []):
                    if user.get('accountEnabled'):
                        email = user.get('userPrincipalName', '').lower()
                        if email:
                            active_users.append(email)
                
                # Check for pagination
                next_link = data.get('@odata.nextLink')
                
                logger.info(f"Retrieved {len(active_users)} users so far...")
            
            # Save to cache
            cache_data = {
                'timestamp': datetime.now().isoformat(),
                'user_count': len(active_users),
                'users': sorted(active_users)
            }
            
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.cache_path, 'w') as f:
                json.dump(cache_data, f, indent=2)
            
            logger.info(f"Sync complete: {len(active_users)} active users cached")
            
            # Send success email
            self.send_notification("AD Sync Successful", 
                f"Successfully synced {len(active_users)} active users from Azure AD")
            
            return True
            
        except Exception as e:
            logger.error(f"Sync failed: {e}", exc_info=True)
            self.send_notification("AD Sync Failed", 
                f"Error during sync: {str(e)}", error=True)
            return False
    
    def send_notification(self, subject, message, error=False):
        """Send email notification"""
        try:
            # Use Microsoft Graph to send email
            if not self.token:
                return
            
            headers = {
                'Authorization': f'Bearer {self.token}',
                'Content-Type': 'application/json'
            }
            
            email_body = {
                "message": {
                    "subject": f"[UserValidation] {subject}",
                    "body": {
                        "contentType": "Text",
                        "content": f"{message}\n\nTimestamp: {datetime.now().isoformat()}"
                    },
                    "toRecipients": [
                        {"emailAddress": {"address": self.config.admin_email}}
                    ]
                },
                "saveToSentItems": "false"
            }
            
            # Send from service account
            url = "https://graph.microsoft.com/v1.0/me/sendMail"
            response = requests.post(url, headers=headers, json=email_body)
            
            if response.status_code == 202:
                logger.info(f"Notification sent: {subject}")
            else:
                logger.warning(f"Failed to send notification: {response.status_code}")
                
        except Exception as e:
            logger.error(f"Error sending notification: {e}", exc_info=True)

def main():
    """Main service loop"""
    try:
        logger.info("=== AD Sync Service Starting ===")
        
        config = Config()
        sync = ADSync(config)
        
        # Initial sync
        logger.info("Performing initial sync...")
        sync.sync_users()
        
        # Schedule regular syncs
        scheduler = BlockingScheduler()
        scheduler.add_job(
            sync.sync_users,
            'interval',
            minutes=config.ad_sync_interval_minutes,
            id='ad_sync',
            name='Active Directory Sync',
            replace_existing=True
        )
        
        logger.info(f"Scheduler configured: sync every {config.ad_sync_interval_minutes} minutes")
        logger.info("=== AD Sync Service Ready ===")
        
        # Start scheduler (blocks forever)
        scheduler.start()
        
    except KeyboardInterrupt:
        logger.info("Service stopped by user")
    except Exception as e:
        logger.error(f"Service crashed: {e}", exc_info=True)
        time.sleep(60)  # Wait before restart
        raise

if __name__ == '__main__':
    main()