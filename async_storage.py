import asyncio
import json
import os
import time
import aiofiles
from mailbox import Maildir
from typing import Dict, Optional, TypedDict, List, cast


class UIDData(TypedDict):
    uidvalidity: int
    uidnext: int
    key_to_uid: Dict[str, int]
    uid_to_key: Dict[int, str]

class MaildirWrapper:
    def __init__(self, path: str):
        self.path = path
        self.maildir = Maildir(self.path, create=True)
        self.uid_file = os.path.join(path, ".uid_mapping")
        self._uid_data = None

    @staticmethod
    def is_maildir(path: str) -> bool:
        """Check if the given path is a valid Maildir directory"""
        return os.path.isdir(path) and os.path.exists(os.path.join(path, 'cur')) and \
               os.path.exists(os.path.join(path, 'new')) and os.path.exists(os.path.join(path, 'tmp'))
        
    async def _load_uid_data(self) -> UIDData:
        """Load UID mapping from file asynchronously"""
        try:
            if os.path.exists(self.uid_file):
                async with aiofiles.open(self.uid_file, 'r') as f:
                    content = await f.read()
                    data = json.loads(content)
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
        except (json.JSONDecodeError, IOError, OSError):
            pass
        
        # Create new UID data
        return {
            'uidvalidity': int(time.time()),
            'uidnext': 1,
            'key_to_uid': {},
            'uid_to_key': {}
        }
    
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
        """Synchronize UIDs with current maildir contents"""
        uid_data = await self._get_uid_data()
        
        # Get current keys (this is the expensive I/O operation)
        current_keys = await asyncio.to_thread(lambda: set(self.maildir.keys()))
        mapped_keys = set(uid_data['key_to_uid'].keys())
        
        # Remove UIDs for deleted messages
        deleted_keys = mapped_keys - current_keys
        for key in deleted_keys:
            uid = uid_data['key_to_uid'].pop(key, None)
            if uid:
                uid_data['uid_to_key'].pop(uid, None)
        
        # Add UIDs for new messages
        new_keys = current_keys - mapped_keys
        for key in new_keys:
            uid = uid_data['uidnext']
            uid_data['key_to_uid'][key] = uid
            uid_data['uid_to_key'][uid] = key
            uid_data['uidnext'] = uid + 1
        
        if deleted_keys or new_keys:
            self._uid_data = uid_data
            await self._save_uid_data()
    
    async def get_uidvalidity(self) -> int:
        """Get UIDVALIDITY value"""
        uid_data = await self._get_uid_data()
        return uid_data['uidvalidity']
    
    async def get_uidnext(self) -> int:
        """Get UIDNEXT value"""
        await self._sync_uids()
        uid_data = await self._get_uid_data()
        return uid_data['uidnext']
    
    async def get_message_count(self) -> int:
        """Get total message count"""
        return await asyncio.to_thread(len, self.maildir)
    
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
            for i, key in enumerate(self.maildir.keys()):
                try:
                    message = self.maildir.get_message(key)
                    if "S" not in message.get_flags():
                        return i + 1  # Sequence numbers are 1-based
                except KeyError:
                    continue
            return None
        
        return await asyncio.to_thread(find_first_unseen)
    
    async def get_folder_attributes(self) -> List[str]:
        attributes : List[str] = []
        
        # \Noselect - folder exists but cannot be selected (no messages)
        # Check if it's just a hierarchy placeholder
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
        # Usually means it has new messages since last check
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

    async def get_uid_from_key(self, key: str) -> int | None:
        """Get the UID of a message by its key"""
        await self._sync_uids()
        if key in cast(UIDData, self._uid_data)['key_to_uid']:
            return cast(UIDData, self._uid_data)['key_to_uid'][key]
        else:
            return None
        
    async def get_key_from_uid(self, uid: int) -> str | None:
        await self._sync_uids()
        if uid in cast(UIDData, self._uid_data)['uid_to_key']:
            return cast(UIDData, self._uid_data)['uid_to_key'][uid]
        else:
            return None