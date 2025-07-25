import logging
import os
import asyncio
import shlex
from typing import List, Tuple
from async_storage import MaildirWrapper

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
                        else:
                            response = self._handle_fetch(tag, args, authenticated_user, selected_folder)
                            
                    elif command == "STORE":
                        if not authenticated_user:
                            response = f"{tag} NO [AUTHENTICATIONFAILED] Not authenticated\r\n"
                        elif not selected_folder:
                            response = f"{tag} NO [CLIENTBUG] No folder selected\r\n"
                        elif not allow_write:
                            response = f"{tag} NO [READ-ONLY] Cannot store in read-only folder\r\n"
                        else:
                            response = self._handle_store(tag, args, authenticated_user, selected_folder)
                            
                    # Phase 2: Essential Commands
                    elif command == "SEARCH":
                        if not authenticated_user:
                            response = f"{tag} NO [AUTHENTICATIONFAILED] Not authenticated\r\n"
                        elif not selected_folder:
                            response = f"{tag} NO [CLIENTBUG] No folder selected\r\n"
                        else:
                            response = self._handle_search(tag, args, authenticated_user, selected_folder)
                            
                    elif command.startswith("UID"):
                        if not authenticated_user:
                            response = f"{tag} NO [AUTHENTICATIONFAILED] Not authenticated\r\n"
                        elif not selected_folder:
                            response = f"{tag} NO [CLIENTBUG] No folder selected\r\n"
                        else:
                            # Handle UID FETCH, UID STORE, UID SEARCH
                            uid_parts = command.split(' ', 1)
                            if len(uid_parts) > 1:
                                uid_command = uid_parts[1].upper()
                                if uid_command == "FETCH":
                                    response = self._handle_uid_fetch(tag, args, authenticated_user, selected_folder)
                                elif uid_command == "STORE":
                                    if allow_write:
                                        response = self._handle_uid_store(tag, args, authenticated_user, selected_folder)
                                    else:
                                        response = f"{tag} NO [READ-ONLY] Cannot store in read-only folder\r\n"
                                elif uid_command == "SEARCH":
                                    response = self._handle_uid_search(tag, args, authenticated_user, selected_folder)
                                else:
                                    response = f"{tag} BAD Invalid UID command\r\n"
                            else:
                                response = f"{tag} BAD UID command requires subcommand\r\n"
                                
                    elif command == "EXPUNGE":
                        if not authenticated_user:
                            response = f"{tag} NO [AUTHENTICATIONFAILED] Not authenticated\r\n"
                        elif not selected_folder:
                            response = f"{tag} NO [CLIENTBUG] No folder selected\r\n"
                        elif not allow_write:
                            response = f"{tag} NO [READ-ONLY] Cannot expunge in read-only folder\r\n"
                        else:
                            response = self._handle_expunge(tag, authenticated_user, selected_folder)
                            
                    elif command == "CLOSE":
                        if not authenticated_user:
                            response = f"{tag} NO [AUTHENTICATIONFAILED] Not authenticated\r\n"
                        elif not selected_folder:
                            response = f"{tag} NO [CLIENTBUG] No folder selected\r\n"
                        else:
                            response = self._handle_close(tag, authenticated_user, selected_folder)
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

    def _handle_fetch(self, tag: str, args: str, user: str, folder: str) -> str:
        # Implementation needed
        return f"{tag} OK FETCH completed\r\n"

    def _handle_store(self, tag: str, args: str, user: str, folder: str) -> str:
        # Implementation needed
        return f"{tag} OK STORE completed\r\n"

    def _handle_search(self, tag: str, args: str, user: str, folder: str) -> str:
        # Implementation needed
        return f"* SEARCH\r\n{tag} OK SEARCH completed\r\n"

    def _handle_uid_fetch(self, tag: str, args: str, user: str, folder: str) -> str:
        # Implementation needed
        return f"{tag} OK UID FETCH completed\r\n"

    def _handle_uid_store(self, tag: str, args: str, user: str, folder: str) -> str:
        # Implementation needed
        return f"{tag} OK UID STORE completed\r\n"

    def _handle_uid_search(self, tag: str, args: str, user: str, folder: str) -> str:
        # Implementation needed
        return f"* SEARCH\r\n{tag} OK UID SEARCH completed\r\n"

    def _handle_expunge(self, tag: str, user: str, folder: str) -> str:
        # Implementation needed
        return f"{tag} OK EXPUNGE completed\r\n"

    def _handle_close(self, tag: str, user: str, folder: str) -> str:
        # Implementation needed (expunge + close)
        return f"{tag} OK CLOSE completed\r\n"

    def _handle_status(self, tag: str, args: str, user: str) -> str:
        # Implementation needed
        return f"* STATUS INBOX (MESSAGES 0 RECENT 0 UIDNEXT 1 UIDVALIDITY 1 UNSEEN 0)\r\n{tag} OK STATUS completed\r\n"