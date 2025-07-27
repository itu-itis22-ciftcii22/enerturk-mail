import tempfile
import os

# Configuration for testing
SMTP_HOST = "127.0.0.1"
SMTP_PORT = 0  # Ephemeral port for SMTP
IMAP_HOST = "127.0.0.1"
IMAP_PORT = 0  # Ephemeral port for IMAP

# Test user credentials
USERNAME = "testuser"
PASSWORD = "password123"

# Base directory for mail storage
BASE_DIR = tempfile.mkdtemp(prefix="mails_")

# Ensure the base directory exists
if not os.path.exists(BASE_DIR):
    os.makedirs(BASE_DIR)
