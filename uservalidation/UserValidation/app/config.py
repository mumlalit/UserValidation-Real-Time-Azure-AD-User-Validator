"""Configuration Management"""
import json
from pathlib import Path

class Config:
    def __init__(self):
        self.install_path = "C:\\UserValidation"
        self._load_config()
    
    def _load_config(self):
        config_path = Path(self.install_path) / 'config' / 'app_config.json'
        
        if config_path.exists():
            with open(config_path, 'r') as f:
                config = json.load(f)
                self.server_ip = config.get('server_ip', '10.228.176.17')
                self.port = config.get('port', 8080)
                self.log_retention_days = config.get('log_retention_days', 90)
                self.max_upload_size_mb = config.get('max_upload_size_mb', 50)
                self.admin_email = config.get('admin_email', 'lalit@csod.com')
                self.batch_size = config.get('batch_size', 10)
                self.max_concurrent_queries = config.get('max_concurrent_queries', 5)
        else:
            self.server_ip = '10.228.176.17'
            self.port = 8080
            self.log_retention_days = 90
            self.max_upload_size_mb = 50
            self.admin_email = 'lalit@csod.com'
            self.batch_size = 10
            self.max_concurrent_queries = 5