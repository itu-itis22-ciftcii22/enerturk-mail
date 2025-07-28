import asyncio
import json
import os
import time
import threading
import aiofiles
from mailbox import Maildir, MaildirMessage
from typing import Dict, Optional, TypedDict, List


class FolderUIDData(TypedDict):
    uidvalidity: int
    uidnext: int
    key_to_uid: Dict[str, int]
    uid_to_key: Dict[int, str]

class UIDData(TypedDict):
    folders: Dict[str, FolderUIDData]

class MaildirWrapper:
    def __init__(self, mailbox_path: str, folder_name: Optional[str] = None, create: bool = False):
        self.base_path = mailbox_path
        base_maildir = Maildir(mailbox_path, create=create)
        
        if folder_name:
            # Handle folder navigation
            parts = folder_name.split("/")
            current = base_maildir
            for part in parts:
                if create:
                    current = current.add_folder(part)
                else:
                    try:
                        current = current.get_folder(part)
                    except FileNotFoundError:
                        raise FileNotFoundError(f"Mailbox folder '{folder_name}' does not exist")
            self.maildir = current
            self.folder_name = folder_name
        else:
            self.maildir = base_maildir
            self.folder_name = ""
        
        # UID file is always at the base path (per-user, not per-folder)
        self.uid_file = os.path.join(self.base_path, ".uid_mapping")
        self._uid_data = None
        self._lock = threading.RLock()

    @property
    def path(self) -> str:
        return self.maildir._path

    def get_keys_safe(self) -> List[str]:
        """Get a thread-safe copy of maildir keys"""
        with self._lock:
            return list(self.maildir.keys())
    
    def get_message_safe(self, key: str) -> Optional[MaildirMessage]:
        """Get a message by key in a thread-safe way"""
        with self._lock:
            try:
                return self.maildir.get_message(key)
            except KeyError:
                return None

    def list_folders_safe(self) -> List[str]:
        """Get a thread-safe list of folder names"""
        with self._lock:
            return self.maildir.list_folders()

    @staticmethod
    def is_maildir(path: str) -> bool:
        """Check if the given path is a valid Maildir directory"""
        return os.path.isdir(path) and os.path.exists(os.path.join(path, 'cur')) and \
               os.path.exists(os.path.join(path, 'new')) and os.path.exists(os.path.join(path, 'tmp'))

    def _get_folder_key(self) -> str:
        """Get the folder key for the UID data structure"""
        return "INBOX" if self.folder_name == "" else self.folder_name

    async def _get_folder_uid_data(self) -> FolderUIDData:
        """Get UID data for the current folder"""
        uid_data = await self._get_uid_data()
        folder_key = self._get_folder_key()
        
        if folder_key not in uid_data['folders']:
            # Ensure unique UIDVALIDITY by adding folder-specific offset
            base_time = int(time.time())
            folder_offset = abs(hash(folder_key)) % 1000  # 0-999 offset based on folder name
            unique_uidvalidity = base_time + folder_offset
            
            uid_data['folders'][folder_key] = {
                'uidvalidity': unique_uidvalidity,
                'uidnext': 1,
                'key_to_uid': {},
                'uid_to_key': {}
            }
            self._uid_data = uid_data
            await self._save_uid_data()
        
        return uid_data['folders'][folder_key]

    async def _load_uid_data(self) -> UIDData:
        """Load UID mapping from file asynchronously"""
        try:
            if os.path.exists(self.uid_file):
                async with aiofiles.open(self.uid_file, 'r') as f:
                    content = await f.read()
                    data = json.loads(content)
                    
                    # Ensure folders dict exists
                    if 'folders' not in data:
                        data['folders'] = {}
                    
                    # Fix integer keys in uid_to_key for all folders
                    for _, folder_data in data['folders'].items():
                        if 'uid_to_key' in folder_data:
                            uid_to_key_fixed = {}
                            for uid_str, key in folder_data['uid_to_key'].items():
                                uid_to_key_fixed[int(uid_str)] = key
                            folder_data['uid_to_key'] = uid_to_key_fixed
                    
                    return data
        except (json.JSONDecodeError, IOError, OSError):
            pass

        # Create new UID data structure
        return {'folders': {}}

    async def _save_uid_data(self):
        """Save UID mapping to file asynchronously"""
        try:
            content = json.dumps(self._uid_data, indent=2)
            async with aiofiles.open(self.uid_file, 'w') as f:
                await f.write(content)
        except IOError as e:
            print(f"Warning: Could not save UID data: {e}")

    async def _get_uid_data(self) -> UIDData:
        """Get UID data, loading if necessary"""
        if self._uid_data is None:
            self._uid_data = await self._load_uid_data()
        return self._uid_data

    async def _sync_uids(self):
        """Synchronize UIDs with current maildir contents for this folder"""
        folder_uid_data = await self._get_folder_uid_data()

        # Get current keys (this is the expensive I/O operation) - thread-safe
        def get_keys_safely():
            with self._lock:
                return set(list(self.maildir.keys()))
        
        current_keys = await asyncio.to_thread(get_keys_safely)
        mapped_keys = set(folder_uid_data['key_to_uid'].keys())

        # Remove UIDs for deleted messages
        deleted_keys = mapped_keys - current_keys
        for key in deleted_keys:
            uid = folder_uid_data['key_to_uid'].pop(key, None)
            if uid:
                folder_uid_data['uid_to_key'].pop(uid, None)

        # Add UIDs for new messages
        new_keys = current_keys - mapped_keys
        for key in new_keys:
            uid = folder_uid_data['uidnext']
            folder_uid_data['key_to_uid'][key] = uid
            folder_uid_data['uid_to_key'][uid] = key
            folder_uid_data['uidnext'] = uid + 1

        if deleted_keys or new_keys:
            await self._save_uid_data()

    async def get_uidvalidity(self) -> int:
        """Get UIDVALIDITY value for this folder"""
        folder_uid_data = await self._get_folder_uid_data()
        return folder_uid_data['uidvalidity']

    async def get_uidnext(self) -> int:
        """Get UIDNEXT value for this folder"""
        await self._sync_uids()
        folder_uid_data = await self._get_folder_uid_data()
        return folder_uid_data['uidnext']

    async def save_message(self, message: MaildirMessage) -> int:
        """Save a message and assign a UID"""
        await self._sync_uids()
        
        def add_message():
            with self._lock:
                return self.maildir.add(message)
        
        key = await asyncio.to_thread(add_message)
        folder_uid_data = await self._get_folder_uid_data()
        uid = folder_uid_data['uidnext']
        folder_uid_data['key_to_uid'][key] = uid
        folder_uid_data['uid_to_key'][uid] = key
        folder_uid_data['uidnext'] += 1
        await self._save_uid_data()
        return uid

    async def load_message_by_uid(self, uid: int) -> Optional[MaildirMessage]:
        """Load a message by its UID"""
        await self._sync_uids()
        folder_uid_data = await self._get_folder_uid_data()
        key = folder_uid_data['uid_to_key'].get(uid)
        if key:
            return self.get_message_safe(key)
        return None

    async def get_message_count(self) -> int:
        """Get total message count"""
        def count_messages():
            with self._lock:
                return len(self.maildir)
        return await asyncio.to_thread(count_messages)

    async def get_recent_count(self) -> int:
        """Get count of recent (new) messages"""
        new_dir = os.path.join(self.path, 'new')

        def count_files():
            if os.path.exists(new_dir):
                return len([f for f in os.listdir(new_dir) 
                           if os.path.isfile(os.path.join(new_dir, f))])
            return 0

        return await asyncio.to_thread(count_files)

    async def get_first_unseen_seq(self) -> Optional[int]:
        """Get sequence number of first unseen message"""
        def find_first_unseen():
            with self._lock:
                keys_list = list(self.maildir.keys())
                
                for i, key in enumerate(keys_list):
                    try:
                        message = self.get_message_safe(key)
                        if message and "S" not in message.get_flags():
                            return i + 1  # Sequence numbers are 1-based
                    except KeyError:
                        continue
                return None

        return await asyncio.to_thread(find_first_unseen)

    async def get_folder_attributes(self) -> List[str]:
        attributes: List[str] = []

        # \Noselect - folder exists but cannot be selected (no messages)
        if not os.path.exists(os.path.join(self.path, "cur")):
            attributes.append("\\Noselect")

        async def has_new_messages(folder_path: str) -> bool:
            """Check if folder has new/unseen messages"""
            new_dir = os.path.join(folder_path, "new")
            try:
                return len(os.listdir(new_dir)) > 0
            except OSError:
                return False

        # \Marked - folder has been marked as "interesting" 
        if await has_new_messages(self.path):
            attributes.append("\\Marked")
        else:
            attributes.append("\\Unmarked")

        async def has_subfolders(folder_path: str) -> bool:
            """Check if folder has any subfolders"""
            try:
                for item in os.listdir(folder_path):
                    item_path = os.path.join(folder_path, item)
                    if os.path.isdir(item_path) and item.startswith("."):
                        return True
                return False
            except OSError:
                return False

        # \HasChildren / \HasNoChildren (IMAP4rev1 extension)
        if await has_subfolders(self.path):
            attributes.append("\\HasChildren")
        else:
            attributes.append("\\HasNoChildren")

        return attributes

    async def get_uid_from_key(self, key: str) -> Optional[int]:
        """Get the UID of a message by its key"""
        await self._sync_uids()
        folder_uid_data = await self._get_folder_uid_data()
        return folder_uid_data['key_to_uid'].get(key)

    async def get_key_from_uid(self, uid: int) -> Optional[str]:
        """Get the key of a message by its UID"""
        await self._sync_uids()
        folder_uid_data = await self._get_folder_uid_data()
        return folder_uid_data['uid_to_key'].get(uid)

    async def mark_message_as_seen(self, key: str) -> bool:
        """Mark a message as seen by moving it to cur/ and adding the Seen flag"""
        def move_and_flag():
            with self._lock:
                try:
                    message = self.maildir.get_message(key)
                    if not message:
                        return False
                    
                    current_flags = message.get_flags()
                    if 'S' not in current_flags:
                        new_flags = current_flags + 'S'
                        message.set_flags(new_flags)
                        self.maildir[key] = message
                        return True
                    return False
                except (KeyError, OSError) as e:
                    print(f"Error marking message as seen: {e}")
                    return False

        return await asyncio.to_thread(move_and_flag)

    async def set_message_flags(self, key: str, flags: str) -> bool:
        """Set flags for a message"""
        def update_flags():
            with self._lock:
                try:
                    message = self.maildir.get_message(key)
                    if not message:
                        return False
                    
                    message.set_flags(flags)
                    self.maildir[key] = message
                    return True
                except (KeyError, OSError) as e:
                    print(f"Error setting message flags: {e}")
                    return False

        return await asyncio.to_thread(update_flags)

    async def get_message_with_seen_flag(self, key: str) -> Optional[MaildirMessage]:
        """Get a message and automatically mark it as seen (for non-PEEK operations)"""
        def get_and_mark():
            with self._lock:
                try:
                    message = self.maildir.get_message(key)
                    if not message:
                        return None
                    
                    current_flags = message.get_flags()
                    if 'S' not in current_flags:
                        new_flags = current_flags + 'S'
                        message.set_flags(new_flags)
                        self.maildir[key] = message
                        message = self.maildir.get_message(key)
                    
                    return message
                except (KeyError, OSError) as e:
                    print(f"Error getting message with seen flag: {e}")
                    return None

        return await asyncio.to_thread(get_and_mark)