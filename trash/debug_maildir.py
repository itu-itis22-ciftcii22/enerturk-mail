#!/usr/bin/env python3
"""Debug script to test Maildir functionality"""

import os
from server.storage_manager import MaildirWrapper
from config import BASE_DIR, USERNAME

def main():
    print(f"BASE_DIR: {BASE_DIR}")
    print(f"USERNAME: {USERNAME}")
    
    inbox_path = os.path.join(BASE_DIR, USERNAME, "Inbox")
    print(f"Inbox path: {inbox_path}")
    print(f"Inbox exists: {os.path.exists(inbox_path)}")
    
    if os.path.exists(inbox_path):
        mailbox = MaildirWrapper(inbox_path)
        print(f"Maildir keys: {list(mailbox.maildir.keys())}")
        print(f"Number of messages: {len(list(mailbox.maildir.keys()))}")
        
        # List files in subdirectories
        for subdir in ['new', 'cur', 'tmp']:
            subdir_path = os.path.join(inbox_path, subdir)
            if os.path.exists(subdir_path):
                files = os.listdir(subdir_path)
                print(f"Files in {subdir}: {files}")

if __name__ == "__main__":
    main()
