import os

# Configuration for testing
SMTP_HOST = "localhost"
SMTP_PORT = 8025  # Fixed port for SMTP
IMAP_HOST = "localhost"
IMAP_PORT = 8143  # Fixed port for IMAP

# Test user credentials
USERNAME = "testuser"
PASSWORD = "password123"

# Base directory for mail storage - use project directory instead of temp
BASE_DIR = os.path.join(os.path.dirname(__file__), "mails")

# Ensure the base directory exists
if not os.path.exists(BASE_DIR):
    os.makedirs(BASE_DIR)
