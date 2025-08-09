import os

HOST_NAME = "localhost"
SMTP_PORT = 8025
IMAP_PORT = 8143

SERVER_STORAGE_PATH = os.path.join(os.path.dirname(__file__), "mails_server")
CLIENT_STORAGE_PATH = os.path.join(os.path.dirname(__file__), "mails_client")

AUTH_TYPE = 'ldap'
LDAP_SERVER_URI = 'ldap://localhost'
LDAP_DOMAIN = 'localhost'
LDAP_BASE_DN = 'DC=localhost,DC='
LDAP_PORT = 8389
LDAP_USE_SSL = False

USERS = {"testuser": "testpassword"}

# Ensure the base directory exists
if not os.path.exists(SERVER_STORAGE_PATH):
    os.makedirs(SERVER_STORAGE_PATH)
if not os.path.exists(CLIENT_STORAGE_PATH):
    os.makedirs(CLIENT_STORAGE_PATH)

import logging

LOG_LEVEL = logging.DEBUG
LOG_FORMAT = '%(asctime)s - %(levelname)s - %(message)s'
def setup_logging():
    """Configure logging for the application."""
    logging.basicConfig(
        level=LOG_LEVEL,
        format=LOG_FORMAT,
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler('email_client.log')
        ]
    )
