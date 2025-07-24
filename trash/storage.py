import json
import os
import time
from mailbox import Maildir
from typing import Dict, Optional
from typing import TypedDict

class UIDData(TypedDict):
    uidvalidity: int
    uidnext: int
    key_to_uid: Dict[str, int]
    uid_to_key: Dict[str, str]

class CustomMaildir:
    def __init__(self, path: str):
        self.path = path
        self.maildir = Maildir(path, create=True)
        self.uid_file = os.path.join(path, ".uid_mapping")
        self.uid_data = self._load_uid_data()
        
    def _load_uid_data(self) -> UIDData:
        """Load UID mapping from file"""
        if os.path.exists(self.uid_file):
            try:
                with open(self.uid_file, 'r') as f:
                    data = json.load(f)
                    # Validate required fields
                    if 'uidvalidity' not in data:
                        data['uidvalidity'] = int(time.time())
                    if 'uidnext' not in data:
                        data['uidnext'] = 1
                    if 'key_to_uid' not in data:
                        data['key_to_uid'] = {}
                    if 'uid_to_key' not in data:
                        data['uid_to_key'] = {}
                    return data
            except (json.JSONDecodeError, IOError):
                pass
        
        # Create new UID data
        return {
            'uidvalidity': int(time.time()),
            'uidnext': 1,
            'key_to_uid': {},
            'uid_to_key': {}
        }
    
    def _save_uid_data(self):
        """Save UID mapping to file"""
        try:
            with open(self.uid_file, 'w') as f:
                json.dump(self.uid_data, f, indent=2)
        except IOError as e:
            print(f"Warning: Could not save UID data: {e}")
    
    def _sync_uids(self):
        """Synchronize UIDs with current maildir contents"""
        current_keys = set(self.maildir.keys())
        mapped_keys = set(self.uid_data['key_to_uid'].keys())
        
        # Remove UIDs for deleted messages
        deleted_keys = mapped_keys - current_keys
        for key in deleted_keys:
            uid = self.uid_data['key_to_uid'].pop(key, None)
            if uid:
                self.uid_data['uid_to_key'].pop(str(uid), None)
        
        # Add UIDs for new messages
        new_keys = current_keys - mapped_keys
        for key in new_keys:
            uid = self.uid_data['uidnext']
            self.uid_data['key_to_uid'][key] = uid
            self.uid_data['uid_to_key'][str(uid)] = key
            self.uid_data['uidnext'] = uid + 1
        
        if deleted_keys or new_keys:
            self._save_uid_data()
    
    def get_uidvalidity(self) -> int:
        """Get UIDVALIDITY value"""
        return self.uid_data['uidvalidity']
    
    def get_uidnext(self) -> int:
        """Get UIDNEXT value"""
        self._sync_uids()
        return self.uid_data['uidnext']
    
    def get_uid_for_key(self, key: str) -> Optional[int]:
        """Get UID for a maildir key"""
        self._sync_uids()
        return self.uid_data['key_to_uid'].get(key)
    
    def get_key_for_uid(self, uid: int) -> Optional[str]:
        """Get maildir key for a UID"""
        self._sync_uids()
        return self.uid_data['uid_to_key'].get(str(uid))
    
    def get_message_count(self) -> int:
        """Get total message count"""
        return len(self.maildir)
    
    def get_recent_count(self) -> int:
        """Get count of recent (new) messages"""
        new_dir = os.path.join(self.path, 'new')
        if os.path.exists(new_dir):
            return len([f for f in os.listdir(new_dir) 
                       if os.path.isfile(os.path.join(new_dir, f))])
        return 0
    
    def get_first_unseen_seq(self) -> Optional[int]:
        """Get sequence number of first unseen message"""
        keys = self.maildir.keys()
        for i, key in enumerate(keys):
            try:
                message = self.maildir.get_message(key)
                if "S" not in message.get_flags() :
                    return i + 1  # Sequence numbers are 1-based
            except KeyError:
                continue
        return None



# Example of how to use in FETCH with UIDs
def handle_uid_fetch(self, tag: str, args: str, user: str, folder: str) -> str:
    """Handle UID FETCH command"""
    dirname = os.path.join(self.base_dir, user, folder)
    mailbox = MaildirWithUIDs(dirname)
    
    # Parse UID range (simplified)
    # args would be like "1001:1005 (FLAGS)"
    parts = args.split(' ', 1)
    uid_range = parts[0]
    fetch_items = parts[1] if len(parts) > 1 else "(FLAGS)"
    
    response = ""
    
    # Handle UID range parsing (this is simplified)
    if ':' in uid_range:
        start_uid, end_uid = map(int, uid_range.split(':'))
        uid_list = range(start_uid, end_uid + 1)
    else:
        uid_list = [int(uid_range)]
    
    seq_num = 1
    for key in mailbox.maildir.keys():
        uid = mailbox.get_uid_for_key(key)
        if uid in uid_list:
            # Build FETCH response with UID
            response += f"* {seq_num} FETCH (UID {uid}"
            
            # Add requested items
            if "FLAGS" in fetch_items:
                flags = mailbox.maildir.get_flags(key)
                imap_flags = maildir_flags_to_imap(flags)
                response += f" FLAGS ({' '.join(imap_flags)})"
            
            response += ")\r\n"
        seq_num += 1
    
    response += f"{tag} OK UID FETCH completed\r\n"
    return response

def maildir_flags_to_imap(maildir_flags: str) -> list[str]:
    """Convert Maildir flags to IMAP flags"""
    flag_map = {
        'S': '\\Seen',
        'R': '\\Answered',
        'F': '\\Flagged',
        'D': '\\Deleted',
        'T': '\\Draft'
    }
    return [flag_map[f] for f in maildir_flags if f in flag_map]


