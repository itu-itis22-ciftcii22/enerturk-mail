import sys
from pathlib import Path
parent_dir = Path(__file__).parent.parent
sys.path.insert(0, str(parent_dir))
from config_reader import ConfigLoader
import logging
from ldap3 import Server, Connection, ALL, SIMPLE

class LDAPAuthenticator:
    def __init__(self, auth_type: str):
        self.auth_type = auth_type
        
        # LDAP settings (only loaded if needed)
        if self.auth_type == 'ldap':
            configs = ConfigLoader()
            self.server_uri = configs.ldap_server_uri
            self.domain = configs.ldap_domain
            self.base_dn = configs.ldap_base_dn
            self.use_ssl = configs.ldap_use_ssl
            self.port = configs.ldap_port
        else:
            self.users = {"testuser@localhost": "testpassword"}
    
    def authenticate_user(self, username: str, password: str) -> bool:
        """Authenticate user based on configured method"""
        if self.auth_type == 'ldap':
            return self._authenticate_ldap(username, password)
        else:
            return self.users.get(username) == password
    
    def _authenticate_ldap(self, username: str, password: str) -> bool:
        """Authenticate user against Active Directory using LDAP"""
        try:
            server = Server(
                self.server_uri,
                port=self.port,
                use_ssl=self.use_ssl,
                get_info=ALL
            )
            
            user_formats = [
                f"{username}@{self.domain}",  # UPN format (most reliable for AD)
                f"CN={username},CN=Users,DC=test,DC=local",  # Full DN format
                f"{self.domain}\\{username}",  # Domain\username format
                username  # Simple username
            ]
        
            
            for user_dn in user_formats:
                with Connection(
                        server,
                        user=user_dn,
                        password=password,
                        authentication=SIMPLE,
                        auto_bind=True
                    ) as conn:
                    if conn.bound:
                        return True  
            return False
            
        except Exception as e:
            logging.error(f"LDAP authentication error: {e}")
            return False

