import sys
from pathlib import Path
parent_dir = Path(__file__).parent.parent
sys.path.insert(0, str(parent_dir))
import config
import logging
from ldap3 import Server, Connection, ALL, NTLM, SIMPLE

class LDAPAuthenticator:
    def __init__(self, auth_type: str):
        self.auth_type = auth_type
        
        # LDAP settings (only loaded if needed)
        if self.auth_type == 'ldap':
            self.server_uri = config.LDAP_SERVER_URI
            self.domain = config.LDAP_DOMAIN
            self.base_dn = config.LDAP_BASE_DN
            self.use_ssl = getattr(config, 'LDAP_USE_SSL', False)
            self.port = getattr(config, 'LDAP_PORT', 389)
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
                f"{self.domain}\\{username}",
                f"{username}@{self.domain.lower()}.com",
                username
            ]
            
            for user_dn in user_formats:
                with Connection(
                        server,
                        user=user_dn,
                        password=password,
                        authentication=NTLM if '\\' in user_dn else SIMPLE,
                        auto_bind=True
                    ) as conn:
                    if conn.bound:
                        return True  
            return False
            
        except Exception as e:
            logging.error(f"LDAP authentication error: {e}")
            return False

