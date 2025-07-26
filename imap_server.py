import logging
import os
import asyncio
import shlex
from typing import List, Tuple
from async_storage import MaildirWrapper
from imap_fetcher import Fetcher

class EnerturkIMAPHandler:

    def __init__(self):
        self.base_dir = "mails/"

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Handle individual IMAP client connection"""
        logging.info(f"IMAP connection from {writer.get_extra_info('peername')}")

        try:
            writer.write(b"* OK [CAPABILITY IMAP4rev1] Simple IMAP Server Ready\r\n")
            await writer.drain()
            
            authenticated_user = None
            selected_folder = None
            allow_write = False
            buffer = b""
            
            while True:
                # Read data in chunks and handle partial lines
                chunk = await reader.read(4096)
                if not chunk:
                    break
                    
                buffer += chunk
                
                # Process complete lines
                while b"\r\n" in buffer:
                    line, buffer = buffer.split(b"\r\n", 1)
                    
                    try:
                        data = line.decode('utf-8').strip()
                    except UnicodeDecodeError:
                        writer.write(b"* BAD Invalid UTF-8 encoding\r\n")
                        await writer.drain()
                        continue
                    
                    if not data:
                        continue
                        
                    logging.debug(f"IMAP received: {data}")
                    
                    # Parse command
                    try:
                        lexer = shlex.shlex(data, posix=True)
                        lexer.whitespace_split = True
                        lexer.quotes = '"'
                        tokens = list(lexer)
                        
                        if len(tokens) < 2:
                            writer.write(f"* BAD Invalid command format\r\n".encode())
                            await writer.drain()
                            continue
                            
                        tag = tokens[0]
                        command = tokens[1].upper()
                        args = tokens[2:] if len(tokens) > 2 else []
                        
                    except Exception:
                        writer.write(b"* BAD Invalid argument syntax\r\n")
                        await writer.drain()
                        continue
                    
                    response : str = ""
                    

                    if command == "CAPABILITY":
                        response = self._handle_capability(tag)
                        
                    elif command == "LOGIN":
                        if authenticated_user:
                            response = f"{tag} NO [ALREADYAUTHENTICATED] Already authenticated\r\n"
                        else:
                            if len(tokens) != 2:
                                response = f"{tag} BAD Invalid LOGIN command format\r\n" 
                            else:
                                response = self._handle_login(tag, tokens[0], tokens[1])
                                if response.startswith(f"{tag} OK"):
                                    authenticated_user = tokens[0]

                    elif command == "LOGOUT":
                        response = f"* BYE IMAP4rev1 Server logging out\r\n{tag} OK LOGOUT completed\r\n"
                        writer.write(response.encode('utf-8'))
                        await writer.drain()
                        return
                        
                    elif command == "SELECT":
                        if not authenticated_user:
                            response = f"{tag} NO [AUTHENTICATIONFAILED] Not authenticated\r\n"
                        else:
                            if len(args) != 1:
                                response = f"{tag} BAD Invalid mailbox name\r\n"
                            else:
                                response = await self._handle_select(tag, args[0], authenticated_user)
                                if response.startswith(f"{tag} OK"):
                                    selected_folder = args[0]
                                    allow_write = True
                                    logging.debug(f"Write state: {allow_write}")
                                
                    elif command == "EXAMINE":
                        if not authenticated_user:
                            response = f"{tag} NO [AUTHENTICATIONFAILED] Not authenticated\r\n"
                        else:
                            if len(args) != 1:
                                response = f"{tag} BAD Invalid mailbox name\r\n"
                            else:
                                response = await self._handle_examine(tag, args[0], authenticated_user)
                                if response.startswith(f"{tag} OK"):
                                    selected_folder = args[0]
                                    allow_write = False
                                    logging.debug(f"Write state: {allow_write}")
                                
                    elif command == "LIST":
                        if not authenticated_user:
                            response = f"{tag} NO [AUTHENTICATIONFAILED] Not authenticated\r\n"
                        else:
                            if len(args) != 2:
                                response = f"{tag} BAD Invalid LIST command format\r\n"
                            else:
                                response = await self._handle_list(tag, args[0], args[1], authenticated_user, selected_folder)

                    elif command == "FETCH":
                        if not authenticated_user:
                            response = f"{tag} NO [AUTHENTICATIONFAILED] Not authenticated\r\n"
                        elif not selected_folder:
                            response = f"{tag} NO [CLIENTBUG] No folder selected\r\n"
                        elif len(args) < 2:
                            response = f"{tag} BAD Invalid FETCH command format\r\n"
                        else:
                            response = await self._handle_fetch(tag, args[0], " ".join(args[1:-1]), authenticated_user, selected_folder)
                            
                    elif command == ("UID FETCH"):
                        if not authenticated_user:
                            response = f"{tag} NO [AUTHENTICATIONFAILED] Not authenticated\r\n"
                        elif not selected_folder:
                            response = f"{tag} NO [CLIENTBUG] No folder selected\r\n"
                        elif len(args) != 2:
                            response = f"{tag} BAD Invalid UID FETCH command format\r\n"
                        else:
                            # Extract UID and data items
                            response = await self._handle_fetch(tag, args[0], " ".join(args[1:-1]), authenticated_user, selected_folder)
                            
                    elif command == "CLOSE":
                        if not authenticated_user:
                            response = f"{tag} NO [AUTHENTICATIONFAILED] Not authenticated\r\n"
                        elif not selected_folder:
                            response = f"{tag} NO [CLIENTBUG] No folder selected\r\n"
                        else:
                            response = f"{tag} OK - close completed, now in authenticated state"
                            selected_folder = None  # Return to authenticated state
                            
                    elif command == "STATUS":
                        if not authenticated_user:
                            response = f"{tag} NO [AUTHENTICATIONFAILED] Not authenticated\r\n"
                        else:
                            response = self._handle_status(tag, args, authenticated_user)
                            
                    # Unrecognized commands
                    else:
                        response = f"{tag} BAD Command '{command}' not recognized\r\n"
                    
                    # Send response
                    if response:
                        writer.write(response.encode('utf-8'))
                        await writer.drain()

        except ConnectionResetError:
            logging.info("IMAP client disconnected")
        except Exception as e:
            logging.error(f"IMAP client error: {e}")
            try:
                writer.write(b"* BYE Server error, closing connection\r\n")
                await writer.drain()
            except:
                pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except:
                pass

    def _handle_capability(self, tag : str) -> str:
        return f"* CAPABILITY IMAP4rev1\r\n{tag} OK CAPABILITY completed\r\n"
    
    def _authenticate_user(self, username: str, password: str) -> bool:
        """Placeholder for user authentication logic"""
        return False
    
    def _handle_login(self, tag: str, username: str, password: str) -> str:
        if self._authenticate_user(username, password):
            return f"{tag} OK LOGIN completed\r\n"
        else:
            return f"{tag} NO [AUTHENTICATIONFAILED] Invalid credentials\r\n"
        
    async def _handle_select(self, tag: str, mailbox_name: str, user: str) -> str:
        dirname = os.path.join(self.base_dir, user, mailbox_name)
        
        # Check if directory exists (quick check)
        if not await asyncio.to_thread(os.path.isdir, dirname):
            return f"{tag} NO [NONMAILBOX] Not a mailbox directory\r\n"
        
        try:
            # Use async UID-aware maildir wrapper
            mailbox = MaildirWrapper(dirname)
            
            # Get mailbox statistics concurrently
            exists, recent, first_unseen, uidvalidity, uidnext = await asyncio.gather(
                mailbox.get_message_count(),
                mailbox.get_recent_count(),
                mailbox.get_first_unseen_seq(),
                mailbox.get_uidvalidity(),
                mailbox.get_uidnext()
            )
            
            # Build response
            response = f"* {exists} EXISTS\r\n"
            response += f"* {recent} RECENT\r\n"
            
            if first_unseen is not None:
                response += f"* OK [UNSEEN {first_unseen}] Message {first_unseen} is first unseen\r\n"
            
            response += f"* FLAGS (\\Answered \\Flagged \\Deleted \\Seen \\Draft)\r\n"
            response += f"* OK [PERMANENTFLAGS (\\Deleted \\Seen \\*)] Limited\r\n"
            response += f"* OK [UIDVALIDITY {uidvalidity}] UIDs valid\r\n"
            response += f"* OK [UIDNEXT {uidnext}] Predicted next UID\r\n"
            response += f"{tag} OK [READ-WRITE] SELECT completed\r\n"
            
            return response
            
        except Exception as e:
            return f"{tag} NO [SERVERFAILURE] Server error: {str(e)}\r\n"

    async def _handle_examine(self, tag: str, mailbox_name: str, user: str) -> str:
        dirname = os.path.join(self.base_dir, user, mailbox_name)
        
        # Check if directory exists (quick check)
        if not await asyncio.to_thread(os.path.isdir, dirname):
            return f"{tag} NO [NONMAILBOX] Not a mailbox directory\r\n"
        
        try:
            # Use async UID-aware maildir wrapper
            mailbox = MaildirWrapper(dirname)
            
            # Get mailbox statistics concurrently
            exists, recent, first_unseen, uidvalidity, uidnext = await asyncio.gather(
                mailbox.get_message_count(),
                mailbox.get_recent_count(),
                mailbox.get_first_unseen_seq(),
                mailbox.get_uidvalidity(),
                mailbox.get_uidnext()
            )
            
            # Build response
            response = f"* {exists} EXISTS\r\n"
            response += f"* {recent} RECENT\r\n"
            
            if first_unseen is not None:
                response += f"* OK [UNSEEN {first_unseen}] Message {first_unseen} is first unseen\r\n"
            
            response += f"* FLAGS (\\Answered \\Flagged \\Deleted \\Seen \\Draft)\r\n"
            response += f"* OK [PERMANENTFLAGS (\\Deleted \\Seen \\*)] Limited\r\n"
            response += f"* OK [UIDVALIDITY {uidvalidity}] UIDs valid\r\n"
            response += f"* OK [UIDNEXT {uidnext}] Predicted next UID\r\n"
            response += f"{tag} OK [READ-WRITE] EXAMINE completed\r\n"
            
            return response
            
        except Exception as e:
            return f"{tag} NO [SERVERFAILURE] Server error: {str(e)}\r\n"

    async def _handle_list(self, tag: str, reference_name: str, mailbox_name: str, user: str, selected_folder: str | None) -> str:

        if ".." in reference_name or ".." in mailbox_name:
            return f"{tag} NO [NONAUTHENTICATED] Invalid reference name\r\n"
        
        base_path = os.path.join(self.base_dir, user)

        if reference_name.startswith("~"):
            # User-specific path
            subpath = reference_name[1:]
            # Security check for path traversal
            if subpath.startswith("/"):
                return f"{tag} NO [NONAUTHENTICATED] Invalid reference name\r\n"
            base_path = os.path.join(base_path, subpath)
        elif selected_folder is None:
            # No folder selected, but trying to use relative reference
            return f"{tag} NO [NOSELECT] No folder selected\r\n"
        else:
            if reference_name.startswith("/"):
                return f"{tag} NO [NONAUTHENTICATED] Invalid reference name\r\n"
            base_path = os.path.join(base_path, selected_folder, reference_name)

        response = ""
        
        if mailbox_name == "":
            # Return hierarchy delimiter info
            response += ('* LIST (\\Noselect) "/" ""\r\n')
        
        elif mailbox_name.endswith("*"):
            base_path = os.path.join(base_path, mailbox_name[:-1])
            
            # Get all matching folders
            if not MaildirWrapper.is_maildir(base_path):
                return f"{tag} NO [NONMAILBOX] Not a mailbox directory\r\n"
            mailbox = MaildirWrapper(base_path)

            folder_names = mailbox.maildir.list_folders()
            folder_paths = [os.path.join(base_path, folder) for folder in folder_names]
            for folder_name, folder_path in zip(folder_names, folder_paths):
                    dfs_stack : List[Tuple[str, str]] = [(folder_name, folder_path)]
                    while dfs_stack:
                        current_folder = dfs_stack.pop(0)
                        if os.path.isdir(current_folder[1]):
                            # Check if it's a mailbox directory
                            if MaildirWrapper.is_maildir(current_folder[1]):
                                submailbox = MaildirWrapper(current_folder[1])
                                attributes = await submailbox.get_folder_attributes()
                                attr_str = " ".join(attributes)
                                response += f'* LIST ({attr_str}) "/" "{current_folder[0]}"\r\n'
                                
                                # Add subfolders to the tree
                                subfolder_names = submailbox.maildir.list_folders()
                                subfolder_paths = [os.path.join(current_folder[1], subfolder) for subfolder in subfolder_names]
                                for subfolder_name, subfolder_path in zip(subfolder_names, subfolder_paths):
                                    full_subfolder_name = f"{current_folder[0]}/{subfolder_name}"
                                    dfs_stack.append((full_subfolder_name, subfolder_path))

        elif mailbox_name.endswith("%"):
            base_path = os.path.join(base_path, mailbox_name[:-1])
            
            # Get all matching folders
            if not MaildirWrapper.is_maildir(base_path):
                return f"{tag} NO [NONMAILBOX] Not a mailbox directory\r\n"
            mailbox = MaildirWrapper(base_path)

            folder_names = mailbox.maildir.list_folders()
            folder_paths = [os.path.join(base_path, folder) for folder in folder_names]
            for folder_name, folder_path in zip(folder_names, folder_paths):
                if os.path.isdir(folder_path):
                    # Check if it's a mailbox directory
                    if MaildirWrapper.is_maildir(folder_path):
                        submailbox = MaildirWrapper(folder_path)
                        attributes = await submailbox.get_folder_attributes()
                        attr_str = " ".join(attributes)
                        response += f'* LIST ({attr_str}) "/" "{folder_name}"\r\n'         

        else:
            if MaildirWrapper.is_maildir(base_path):
                mailbox = MaildirWrapper(base_path)
                attributes = await mailbox.get_folder_attributes()
                attr_str = " ".join(attributes)
                response += f'* LIST ({attr_str}) "/" "{mailbox_name}"\r\n'
            else:
                return f"{tag} NO [NONEXISTENT] Mailbox does not exist\r\n"

        return f'{response}{tag} OK LIST completed\r\n'

    async def _handle_fetch(self, tag: str, sequences: str, item_names: str, user: str, selected_folder: str) -> str:
        # Handle sequence range parsing
        if ':' in sequences:
            start_seq, end_seq = map(int, sequences.split(':'))
            seq_list = range(start_seq, end_seq + 1)
        else:
            seq_list = [int(sequences)]
        
        # Remove parentheses if present
        if item_names.startswith('(') and item_names.endswith(')'):
            item_names = item_names[1:-1]
        
        # Split into individual items
        items = item_names.split()

        dirname = os.path.join(self.base_dir, user, selected_folder)
        mailbox = MaildirWrapper(dirname)
        message_keys = mailbox.maildir.keys()
        message_uid_key_pairs : List[Tuple[int, str]] = []
        for key in message_keys:
            uid = await mailbox.get_uid_from_key(key)
            if uid is not None:
                message_uid_key_pairs.append((uid, key))
        message_uid_key_pairs = sorted(message_uid_key_pairs, key=lambda pair: pair[0])

        # Macro expansions
        MACROS = {
            'ALL': ['FLAGS', 'INTERNALDATE', 'RFC822.SIZE', 'ENVELOPE'],
            'FAST': ['FLAGS', 'INTERNALDATE', 'RFC822.SIZE'],
            'FULL': ['FLAGS', 'INTERNALDATE', 'RFC822.SIZE', 'ENVELOPE', 'BODY']
        }
        
        # Handle single macro (must be alone)
        if len(items) == 1 and items[0].upper() in MACROS:
            items = MACROS[items[0].upper()]

        fetcher = Fetcher()
        response = ""
        
        for seq in seq_list:
            # Convert 1-based sequence to 0-based index
            if seq < 1 or seq > len(message_uid_key_pairs):
                continue  # Skip invalid sequence numbers
            
            index = seq - 1  # Convert to 0-based index

            message = mailbox.maildir.get_message(message_uid_key_pairs[index][1])
            
            # Build fetch items response
            fetch_items : List[str] = []
            for item in items:
                fetch_items.append(fetcher.handle_fetch_item(item, message))
            
            # Format response properly
            if len(fetch_items) == 1:
                response += f"* {seq} FETCH {fetch_items[0]}\r\n"
            else:
                response += f"* {seq} FETCH ({' '.join(fetch_items)})\r\n"

        response += f"{tag} OK FETCH completed\r\n"
        return response

    async def _handle_uid_fetch(self, tag: str, uids: str, item_names: str, user: str, selected_folder: str) -> str:
        # Handle UID range parsing
        if ':' in uids:
            start_uid, end_uid = map(int, uids.split(':'))
            uid_list = range(start_uid, end_uid + 1)
        else:
            uid_list = [int(uids)]
        
        # Remove parentheses if present
        if item_names.startswith('(') and item_names.endswith(')'):
            item_names = item_names[1:-1]
        
        # Split into individual items
        items = item_names.split()

        dirname = os.path.join(self.base_dir, user, selected_folder)
        mailbox = MaildirWrapper(dirname)
        
        # Macro expansions
        MACROS = {
            'ALL': ['FLAGS', 'INTERNALDATE', 'RFC822.SIZE', 'ENVELOPE'],
            'FAST': ['FLAGS', 'INTERNALDATE', 'RFC822.SIZE'],
            'FULL': ['FLAGS', 'INTERNALDATE', 'RFC822.SIZE', 'ENVELOPE', 'BODY']
        }
        
        # Handle single macro (must be alone)
        if len(items) == 1 and items[0].upper() in MACROS:
            items = MACROS[items[0].upper()]

        fetcher = Fetcher()
        response = ""
        
        for uid in uid_list:
            try:
                # Get key for this specific UID
                key = await mailbox.get_key_from_uid(uid)
                if key is None:
                    continue  # Skip UIDs that don't exist
                
                message = mailbox.maildir.get_message(key)
                
                # Build fetch items response
                fetch_items: List[str] = []
                for item in items:
                    fetch_items.append(fetcher.handle_fetch_item(item, message))
                
                # Format response properly - note that UID FETCH responses include the UID
                if len(fetch_items) == 1:
                    response += f"* {uid} FETCH (UID {uid} {fetch_items[0]})\r\n"
                else:
                    response += f"* {uid} FETCH (UID {uid} {' '.join(fetch_items)})\r\n"
                    
            except Exception:
                # Skip UIDs that cause errors (e.g., don't exist)
                continue

        response += f"{tag} OK UID FETCH completed\r\n"
        return response

    def _handle_status(self, tag: str, args: str, user: str) -> str:
        # Implementation needed
        return f"* STATUS INBOX (MESSAGES 0 RECENT 0 UIDNEXT 1 UIDVALIDITY 1 UNSEEN 0)\r\n{tag} OK STATUS completed\r\n"