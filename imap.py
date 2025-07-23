# Copyright 2014-2021 The aiosmtpd Developers
# SPDX-License-Identifier: Apache-2.0

import asyncio
import email
import json
import logging
import os
import sqlite3
import sys
import uuid
from datetime import datetime
from email.mime.text import MIMEText
from functools import lru_cache
from pathlib import Path
from smtplib import SMTP as SMTPClient
from typing import Dict, List, Optional
import socket
import threading

import dns.resolver
from aiosmtpd.controller import Controller
from aiosmtpd.smtp import AuthResult, LoginPassword
from ldap3 import Server, Connection, ALL, NTLM
from ldap3.core.exceptions import LDAPException


DEST_PORT = 25

# LDAP Configuration
LDAP_SERVER = "ldap://your-ad-server.domain.com"
LDAP_PORT = 389
LDAP_USE_SSL = False
LDAP_DOMAIN = "YOURDOMAIN"
LDAP_BASE_DN = "dc=yourdomain,dc=com"

# Mail Storage Configuration
MAIL_STORAGE_PATH = Path("/var/mail/storage")
MAIL_DB_PATH = Path("/var/mail/mail.db")
LOCAL_DOMAINS = {"yourdomain.com", "example.com"}

# Server Ports
SMTP_PORT = 8025
IMAP_PORT = 8143


class MailStorage:
    """Handles email storage with database metadata and file content"""
    
    def __init__(self, storage_path: Path, db_path: Path):
        self.storage_path = storage_path
        self.db_path = db_path
        self._init_storage()
        self._init_database()
    
    def _init_storage(self):
        """Initialize mail storage directories"""
        self.storage_path.mkdir(parents=True, exist_ok=True)
        os.chmod(self.storage_path, 0o700)
    
    def _init_database(self):
        """Initialize mail metadata database"""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS emails (
                id TEXT PRIMARY KEY,
                recipient TEXT NOT NULL,
                sender TEXT NOT NULL,
                subject TEXT,
                date_received TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                size INTEGER,
                file_path TEXT NOT NULL,
                read_status INTEGER DEFAULT 0,
                flags TEXT DEFAULT '',
                folder TEXT DEFAULT 'INBOX',
                uid INTEGER
            )
        """)
        
        conn.execute("""
            CREATE TABLE IF NOT EXISTS folders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                folder_name TEXT NOT NULL,
                UNIQUE(username, folder_name)
            )
        """)
        
        conn.execute("CREATE INDEX IF NOT EXISTS idx_recipient ON emails(recipient)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_date ON emails(date_received)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_uid ON emails(uid)")
        
        conn.commit()
        conn.close()
        os.chmod(self.db_path, 0o600)
    
    def store_email(self, recipient: str, sender: str, raw_content: bytes) -> str:
        """Store an email and return its unique ID"""
        email_id = str(uuid.uuid4())
        
        try:
            msg = email.message_from_bytes(raw_content)
            subject = msg.get("Subject", "No Subject")
        except Exception as e:
            logging.warning(f"Failed to parse email: {e}")
            subject = "No Subject"
        
        user_dir = self.storage_path / recipient.lower()
        user_dir.mkdir(exist_ok=True)
        os.chmod(user_dir, 0o700)
        
        file_path = user_dir / f"{email_id}.eml"
        with open(file_path, 'wb') as f:
            f.write(raw_content)
        os.chmod(file_path, 0o600)
        
        # Get next UID for this user
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute(
            "SELECT MAX(uid) FROM emails WHERE recipient = ?", 
            (recipient.lower(),)
        )
        max_uid = cursor.fetchone()[0] or 0
        next_uid = max_uid + 1
        
        conn.execute("""
            INSERT INTO emails (id, recipient, sender, subject, size, file_path, uid)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (email_id, recipient.lower(), sender, subject, len(raw_content), str(file_path), next_uid))
        conn.commit()
        conn.close()
        
        logging.info(f"Stored email {email_id} for {recipient} with UID {next_uid}")
        return email_id
    
    def get_user_emails(self, username: str, folder: str = "INBOX", limit: int = 50) -> List[Dict]:
        """Retrieve email metadata for a user"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute("""
            SELECT id, sender, subject, date_received, size, read_status, flags, uid
            FROM emails 
            WHERE recipient = ? AND folder = ?
            ORDER BY uid
            LIMIT ?
        """, (username.lower(), folder, limit))
        
        emails = []
        for row in cursor.fetchall():
            emails.append({
                'id': row[0],
                'sender': row[1],
                'subject': row[2],
                'date_received': row[3],
                'size': row[4],
                'read_status': bool(row[5]),
                'flags': row[6].split(',') if row[6] else [],
                'uid': row[7]
            })
        
        conn.close()
        return emails
    
    def get_email_content(self, email_id: str, username: str) -> Optional[bytes]:
        """Retrieve full email content if user has access"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute("""
            SELECT file_path FROM emails 
            WHERE id = ? AND recipient = ?
        """, (email_id, username.lower()))
        
        result = cursor.fetchone()
        conn.close()
        
        if not result:
            return None
        
        try:
            with open(result[0], 'rb') as f:
                return f.read()
        except FileNotFoundError:
            logging.error(f"Email file not found: {result[0]}")
            return None
    
    def get_email_by_uid(self, username: str, uid: int) -> Optional[Dict]:
        """Get email by UID"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute("""
            SELECT id, sender, subject, date_received, size, read_status, flags, file_path
            FROM emails 
            WHERE recipient = ? AND uid = ?
        """, (username.lower(), uid))
        
        result = cursor.fetchone()
        conn.close()
        
        if not result:
            return None
        
        return {
            'id': result[0],
            'sender': result[1],
            'subject': result[2],
            'date_received': result[3],
            'size': result[4],
            'read_status': bool(result[5]),
            'flags': result[6].split(',') if result[6] else [],
            'file_path': result[7],
            'uid': uid
        }


class LDAPAuthenticator:
    def __init__(self, server_uri, domain, base_dn, use_ssl=False, port=389):
        self.server_uri = server_uri
        self.domain = domain
        self.base_dn = base_dn
        self.use_ssl = use_ssl
        self.port = port
        
    def authenticate_user(self, username, password):
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
                try:
                    conn = Connection(
                        server,
                        user=user_dn,
                        password=password,
                        authentication=NTLM if '\\' in user_dn else 'SIMPLE',
                        auto_bind=True
                    )
                    conn.unbind()
                    logging.info(f"LDAP authentication successful for user: {username}")
                    return True
                except LDAPException:
                    continue
                    
            logging.warning(f"LDAP authentication failed for user: {username}")
            return False
            
        except Exception as e:
            logging.error(f"LDAP authentication error: {e}")
            return False

    def __call__(self, server, session, envelope, mechanism, auth_data):
        fail_nothandled = AuthResult(success=False, handled=False)
        
        if mechanism not in ("LOGIN", "PLAIN"):
            return fail_nothandled
            
        if not isinstance(auth_data, LoginPassword):
            return fail_nothandled
            
        username = auth_data.login
        password = auth_data.password
        
        if self.authenticate_user(username, password):
            return AuthResult(success=True)
        else:
            return fail_nothandled


@lru_cache(maxsize=256)
def get_mx(domain):
    try:
        records = dns.resolver.resolve(domain, "MX")
        if not records:
            return None
        result = min(records, key=lambda r: r.preference)
        return str(result.exchange)
    except Exception as e:
        logging.error(f"Failed to resolve MX for {domain}: {e}")
        return None


class EnhancedRelayHandler:
    def __init__(self, mail_storage: MailStorage, local_domains: set):
        self.mail_storage = mail_storage
        self.local_domains = local_domains
    
    def handle_data(self, server, session, envelope, data):
        """Handle incoming email - store locally or relay externally"""
        local_recipients = []
        external_recipients = []
        
        for rcpt in envelope.rcpt_tos:
            _, _, domain = rcpt.partition("@")
            if domain.lower() in self.local_domains:
                local_recipients.append(rcpt)
            else:
                external_recipients.append(rcpt)
        
        for recipient in local_recipients:
            try:
                self.mail_storage.store_email(
                    recipient=recipient,
                    sender=envelope.mail_from,
                    raw_content=envelope.original_content
                )
            except Exception as e:
                logging.error(f"Failed to store email for {recipient}: {e}")
        
        if external_recipients:
            self._relay_external_emails(envelope, external_recipients)
    
    def _relay_external_emails(self, envelope, recipients):
        """Relay emails to external domains"""
        mx_rcpt: Dict[str, list[str]] = {}
        
        for rcpt in recipients:
            _, _, domain = rcpt.partition("@")
            mx = get_mx(domain)
            if mx is None:
                logging.warning(f"No MX record found for domain: {domain}")
                continue
            mx_rcpt.setdefault(mx, []).append(rcpt)

        for mx, rcpts in mx_rcpt.items():
            try:
                with SMTPClient(mx, 25) as client:
                    client.sendmail(
                        from_addr=envelope.mail_from,
                        to_addrs=rcpts,
                        msg=envelope.original_content
                    )
                logging.info(f"Successfully relayed message to {mx} for recipients: {rcpts}")
            except Exception as e:
                logging.error(f"Failed to relay message to {mx}: {e}")


class SimpleIMAPServer:
    """Basic IMAP server implementation"""
    
    def __init__(self, mail_storage: MailStorage, authenticator: LDAPAuthenticator, port=8143):
        self.mail_storage = mail_storage
        self.authenticator = authenticator
        self.port = port
        self.running = False
    
    def start(self):
        """Start the IMAP server"""
        self.running = True
        server_thread = threading.Thread(target=self._run_server, daemon=True)
        server_thread.start()
        logging.info(f"IMAP server started on port {self.port}")
    
    def stop(self):
        """Stop the IMAP server"""
        self.running = False
    
    def _run_server(self):
        """Run the IMAP server loop"""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
            server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server_socket.bind(('', self.port))
            server_socket.listen(5)
            server_socket.settimeout(1.0)  # Allow checking self.running
            
            while self.running:
                try:
                    client_socket, address = server_socket.accept()
                    client_thread = threading.Thread(
                        target=self._handle_client,
                        args=(client_socket, address),
                        daemon=True
                    )
                    client_thread.start()
                except socket.timeout:
                    continue
                except Exception as e:
                    if self.running:
                        logging.error(f"IMAP server error: {e}")
    
    def _handle_client(self, client_socket, address):
        """Handle individual IMAP client connection"""
        logging.info(f"IMAP connection from {address}")
        
        try:
            client_socket.send(b"* OK Simple IMAP Server Ready\r\n")
            
            authenticated_user = None
            selected_folder = None
            
            while True:
                data = client_socket.recv(1024).decode('utf-8').strip()
                if not data:
                    break
                
                logging.debug(f"IMAP received: {data}")
                parts = data.split(' ', 2)
                
                if len(parts) < 2:
                    client_socket.send(b"* BAD Invalid command\r\n")
                    continue
                
                tag = parts[0]
                command = parts[1].upper()
                args = parts[2] if len(parts) > 2 else ""
                
                if command == "CAPABILITY":
                    response = self._handle_capability(tag)
                elif command == "LOGIN":
                    response = self._handle_login(tag, args)
                    if "OK" in response:
                        authenticated_user = args.split()[0].strip('"')
                elif command == "SELECT":
                    if authenticated_user:
                        response = self._handle_select(tag, args, authenticated_user)
                        if "OK" in response:
                            selected_folder = args.strip('"')
                    else:
                        response = f"{tag} NO Not authenticated\r\n"
                elif command == "FETCH":
                    if authenticated_user and selected_folder:
                        response = self._handle_fetch(tag, args, authenticated_user)
                    else:
                        response = f"{tag} NO Not authenticated or no folder selected\r\n"
                elif command == "LIST":
                    if authenticated_user:
                        response = self._handle_list(tag, args)
                    else:
                        response = f"{tag} NO Not authenticated\r\n"
                elif command == "SEARCH":
                    response = f"{tag} NO SEARCH command not implemented\r\n"
                elif command == "STORE":
                    response = f"{tag} NO STORE command not implemented\r\n"
                elif command == "IDLE":
                    response = f"{tag} NO IDLE command not implemented\r\n"
                elif command == "LOGOUT":
                    response = f"* BYE IMAP4rev1 Server logging out\r\n{tag} OK LOGOUT completed\r\n"
                    client_socket.send(response.encode())
                    break
                else:
                    response = f"{tag} BAD Command '{command}' not recognized\r\n"
                
                client_socket.send(response.encode())
                
        except Exception as e:
            logging.error(f"IMAP client error: {e}")
        finally:
            client_socket.close()
    
    def _handle_capability(self, tag):
        """Handle IMAP CAPABILITY command"""
        # Advertise only the commands we actually support
        capabilities = [
            "IMAP4rev1",
            "LOGIN", 
            "SELECT",
            "FETCH",
            "LIST",
            "LOGOUT"
        ]
        response = f"* CAPABILITY {' '.join(capabilities)}\r\n"
        response += f"{tag} OK CAPABILITY completed\r\n"
        return response
    
    def _handle_list(self, tag, args):
        """Handle IMAP LIST command - basic implementation"""
        # Simple implementation - just show INBOX
        response = '* LIST () "/" "INBOX"\r\n'
        response += f"{tag} OK LIST completed\r\n"
        return response
        """Handle IMAP LOGIN command"""
        try:
            parts = args.split(' ', 1)
            username = parts[0].strip('"')
            password = parts[1].strip('"')
            
            if self.authenticator.authenticate_user(username, password):
                return f"{tag} OK LOGIN completed\r\n"
            else:
                return f"{tag} NO LOGIN failed\r\n"
        except:
            return f"{tag} BAD LOGIN arguments invalid\r\n"
    
    def _handle_select(self, tag, args, username):
        """Handle IMAP SELECT command"""
        folder = args.strip('"')
        emails = self.mail_storage.get_user_emails(username, folder)
        count = len(emails)
        
        response = f"* {count} EXISTS\r\n"
        response += f"* 0 RECENT\r\n"
        response += f"* FLAGS (\\Seen \\Answered \\Flagged \\Deleted \\Draft)\r\n"
        response += f"{tag} OK [READ-WRITE] SELECT completed\r\n"
        
        return response
    
    def _handle_fetch(self, tag, args, username):
        """Handle IMAP FETCH command"""
        try:
            # Simple implementation - fetch message by sequence number
            parts = args.split(' ', 1)
            seq_num = int(parts[0])
            fetch_items = parts[1] if len(parts) > 1 else "RFC822"
            
            emails = self.mail_storage.get_user_emails(username, "INBOX")
            if seq_num < 1 or seq_num > len(emails):
                return f"{tag} NO Message not found\r\n"
            
            email_info = emails[seq_num - 1]
            
            if "RFC822" in fetch_items.upper():
                content = self.mail_storage.get_email_content(email_info['id'], username)
                if content:
                    response = f"* {seq_num} FETCH (RFC822 {{{len(content)}}}\r\n"
                    response += content.decode('utf-8', errors='replace')
                    response += f")\r\n{tag} OK FETCH completed\r\n"
                    return response
            
            return f"{tag} NO FETCH failed\r\n"
            
        except Exception as e:
            logging.error(f"FETCH error: {e}")
            return f"{tag} BAD FETCH arguments invalid\r\n"


async def amain():
    # Initialize components
    mail_storage = MailStorage(MAIL_STORAGE_PATH, MAIL_DB_PATH)
    authenticator = LDAPAuthenticator(
        server_uri=LDAP_SERVER,
        domain=LDAP_DOMAIN,
        base_dn=LDAP_BASE_DN,
        use_ssl=LDAP_USE_SSL,
        port=LDAP_PORT if not LDAP_USE_SSL else 636
    )
    
    # Start SMTP server
    handler = EnhancedRelayHandler(mail_storage, LOCAL_DOMAINS)
    smtp_controller = Controller(
        handler,
        hostname='',
        port=SMTP_PORT,
        authenticator=authenticator
    )
    
    # Start IMAP server
    imap_server = SimpleIMAPServer(mail_storage, authenticator, IMAP_PORT)
    
    try:
        smtp_controller.start()
        imap_server.start()
        
        logging.info(f"SMTP server started on port {SMTP_PORT}")
        logging.info(f"IMAP server started on port {IMAP_PORT}")
        
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("User abort indicated")
    finally:
        smtp_controller.stop()
        imap_server.stop()


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    
    try:
        import ldap3
    except ImportError:
        print("Please install ldap3: pip install ldap3")
        sys.exit(1)
    
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        print("Server stopped by user")