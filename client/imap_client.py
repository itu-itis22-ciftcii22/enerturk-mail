import sys
from pathlib import Path
parent_dir = Path(__file__).parent.parent
sys.path.insert(0, str(parent_dir))
from config import HOST_NAME, IMAP_PORT, CLIENT_STORAGE_PATH, setup_logging
setup_logging()
import imaplib
import email
import os
import ssl
import logging
from email import policy
from typing import List, Tuple

def list_mailboxes(imap: imaplib.IMAP4) -> List[str]:
    """List all available mailboxes/folders."""
    status, mailbox_list = imap.list()
    if status != 'OK':
        logging.error(f"Error listing mailboxes: {mailbox_list}")
        return []
    
    mailboxes: List[str] = []
    for mb in mailbox_list:
        # Explicitly check if element is bytes before decoding
        if isinstance(mb, bytes):
            decoded = mb.decode('utf-8')  # Specify encoding explicitly
        else:
            # Fallback to string conversion if not bytes
            decoded = str(mb)
        
        if '"' in decoded:
            mailbox_name = decoded.split('"')[-2]
            mailboxes.append(mailbox_name)
    return mailboxes

def save_email(mailbox_path: str, message_data: bytes) -> None:
    """Save an email message to the specified mailbox directory."""
    msg = email.message_from_bytes(message_data, policy=policy.default)
    message_id = msg.get('Message-ID', f'msg-{hash(msg.as_string())}.eml')
    filename = ''.join(c if c.isalnum() or c in '-_.' else '_' for c in message_id)
    
    os.makedirs(mailbox_path, exist_ok=True)
    
    with open(os.path.join(mailbox_path, filename), 'wb') as f:
        f.write(message_data)
    logging.info(f"Saved message: {filename}")

def replicate_mailbox(imap: imaplib.IMAP4, mailbox_name: str, local_dir: str) -> None:
    #Replicate a single mailbox.
    logging.info(f"Replicating mailbox: {mailbox_name}")
    
    status, data = imap.select(mailbox_name, readonly=True)
    logging.debug(f"Select({mailbox_name}) => status: {status}, data: {data}")
    if status != 'OK':
        logging.error(f"Error selecting mailbox {mailbox_name}: {data}")
        return

    # Handle potential None case before accessing data[0]
    if data[0] is None:
        logging.error(f"Missing message count data in mailbox {mailbox_name}")
        return

    message_count = int(data[0])
    logging.debug(f"Message count in {mailbox_name}: {message_count}")
    
    if message_count == 0:
        logging.warning(f"No messages in mailbox {mailbox_name}")
        return
    
    local_mailbox_dir = os.path.join(local_dir, mailbox_name)
    os.makedirs(local_mailbox_dir, exist_ok=True)
    
    fetch_uid = "1:*"
    fetch_items = "(UID)"
    status, msg_data = imap.fetch(fetch_uid, fetch_items)
    logging.debug(f"Fetch({fetch_uid} {fetch_items}) => status: {status}, msg_data: {msg_data}")
    if status != 'OK':
        logging.error(f"Error fetching UIDs: {msg_data}")
        return

    uids: List[str] = []
    for idx, response in enumerate(msg_data):
        logging.debug(f"msg_data[{idx}]: {response!r}")
        line = None
        if isinstance(response, tuple):
            line = response[0]
            logging.debug(f"Response is tuple, line: {line!r}")
        elif isinstance(response, bytes):
            line = response
            logging.debug(f"Response is bytes, line: {line!r}")
        else:
            logging.debug(f"Response is unknown type: {type(response)}")
            continue

        if not line:
            logging.debug(f"Line is empty, skipping")
            continue

        try:
            parts = line.decode().replace('(', '').replace(')', '').split()
            logging.debug(f"Parts: {parts}")
            if 'UID' in parts:
                idx_uid = parts.index('UID')
                if idx_uid + 1 < len(parts):
                    uids.append(parts[idx_uid + 1])
                    logging.debug(f"Found UID: {parts[idx_uid + 1]}")
        except Exception as e:
            logging.warning(f"Exception decoding line: {e}, line: {line!r}")

    logging.debug(f"Found UIDs: {uids} in mailbox {mailbox_name}")

    for uid in uids:
        logging.info(f"Fetching message UID {uid} of {message_count}...")
        status, msg_data = imap.uid('FETCH', uid, "(RFC822)")
        if status != 'OK':
            logging.error(f"Error fetching message UID {uid}: {msg_data}")
            continue

        try:
            raw_email = msg_data[0][1]
            logging.debug(f"Retrieved email UID {uid}, size: {len(raw_email)} bytes")
            save_email(local_mailbox_dir, raw_email)
        except Exception as e:
            logging.error(f"Exception saving email UID {uid}: {e}")

def authenticate_plain(imap: imaplib.IMAP4, username: str, password: str) -> Tuple[str, str]:
    """Authenticate using PLAIN method."""
    auth_string = f'\0{username}\0{password}'
    auth_bytes = auth_string.encode('utf-8')
    return imap.authenticate('PLAIN', lambda x: auth_bytes)

def main():
    #username = input("Enter email username: ")
    #password = getpass.getpass("Enter email password: ")
    username = "testuser@localhost"
    password = "testpassword"
    
    local_dir = os.path.join(os.getcwd(), CLIENT_STORAGE_PATH, username)
    os.makedirs(local_dir, exist_ok=True)
    
    logging.info(f"Connecting to IMAP server at {HOST_NAME}:{IMAP_PORT}...")
    
    try:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        imap = imaplib.IMAP4_SSL(HOST_NAME, IMAP_PORT, ssl_context=context)
        logging.info("Connected using SSL/TLS")
    except Exception as e:
        logging.warning(f"SSL connection failed: {e}")
        logging.info("Trying plain connection...")
        try:
            imap = imaplib.IMAP4(HOST_NAME, IMAP_PORT)
            try:
                imap.starttls()
                logging.info("Upgraded to TLS connection")
            except:
                logging.warning("Using plain connection (no encryption)")
        except Exception as conn_err:
            logging.error(f"Connection failed: {conn_err}")
            return
    
    try:
        logging.info(f"Authenticating as {username}...")
        status, data = authenticate_plain(imap, username, password)
        if status != 'OK':
            logging.error(f"Authentication failed: {data}")
            return
        
        logging.info("Authentication successful")
        
        mailboxes = list_mailboxes(imap)
        logging.info(f"Found {len(mailboxes)} mailboxes: {', '.join(mailboxes)}")
        
        for mailbox in mailboxes:
            replicate_mailbox(imap, mailbox, local_dir)
        
        logging.info(f"Mail replication complete. Local copy saved to: {local_dir}")
        
    except Exception as e:
        logging.exception(f"An error occurred: {e}")
    finally:
        try:
            imap.logout()
        except:
            pass

if __name__ == "__main__":
    main()