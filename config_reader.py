import json
import logging
from typing import Dict, Any


class ConfigLoader:
    
    def __init__(self, config_file_path: str = "config.json"):
        self.config_file_path = config_file_path
        self.config = self._load_config()
        self.setup_logging()
    
    def _load_config(self) -> Dict[str, Any]:
        try:
            with open(self.config_file_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            raise Exception(f"Error in opening config file: {e}")
    
    def setup_logging(self) -> None:
        log_config = self.config['logging']
        
        log_level = getattr(logging, log_config['log_level'].upper())
        
        logging.basicConfig(
            level=log_level,
            format=log_config['log_format'],
            handlers=[
                logging.StreamHandler(),
                logging.FileHandler(log_config['log_file'])
            ]
        )
    
    @property
    def host_name(self) -> str:
        return self.config['server']['host_name']
    
    @property
    def smtp_port(self) -> int:
        return self.config['server']['smtp_port']
    
    @property
    def imap_port(self) -> int:
        return self.config['server']['imap_port']
    
    @property
    def server_storage_path(self) -> str:
        return self.config['storage']['server_storage_path']
    
    @property
    def client_storage_path(self) -> str:
        return self.config['storage']['client_storage_path']
    
    @property
    def auth_type(self) -> str:
        return self.config['authentication']['auth_type']
    
    @property
    def ldap_server_uri(self) -> str:
        return self.config['authentication']['ldap_server_uri']
    
    @property
    def ldap_domain(self) -> str:
        return self.config['authentication']['ldap_domain']
    
    @property
    def ldap_base_dn(self) -> str:
        return self.config['authentication']['ldap_base_dn']
    
    @property
    def ldap_port(self) -> int:
        return self.config['authentication']['ldap_port']
    
    @property
    def ldap_use_ssl(self) -> bool:
        return self.config['authentication']['ldap_use_ssl']
    
    @property
    def users(self) -> Dict[str, str]:
        return self.config['users']
    
    def get_config_section(self, section: str) -> Dict[str, Any]:
        if section not in self.config:
            raise KeyError(f"Configuration section '{section}' not found")
        return self.config[section]
    
    def get_config_value(self, section: str, key: str) -> Any:
        if section not in self.config:
            raise KeyError(f"Configuration section '{section}' not found")
        if key not in self.config[section]:
            raise KeyError(f"Configuration key '{key}' not found in section '{section}'")
        return self.config[section][key]