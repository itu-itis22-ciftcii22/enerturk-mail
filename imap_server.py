import logging
import os
import asyncio
import shlex
# from typing import
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
                    parts = data.split()
                    if len(parts) < 2:
                        writer.write(b"* BAD Invalid command format\r\n")
                        await writer.drain()
                        continue
                    tag = parts[0]
                    command = parts[1].upper()
                    args = ' '.join(parts[2:]) if len(parts) > 2 else ""

                    try:
                        lexer = shlex.shlex(args, posix=True)
                        lexer.whitespace_split = True
                        lexer.quotes = '"'
                        tokens = list(lexer)
                    except Exception:
                        writer.write(f"{tag} BAD Invalid argument syntax\r\n".encode())
                        await writer.drain()
                        continue
                    
                    response = ""
                    

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
                            if len(tokens) != 1:
                                response = f"{tag} BAD Invalid mailbox name\r\n"
                            else:
                                response = await self._handle_select(tag, tokens[0], authenticated_user)
                                if response.startswith(f"{tag} OK"):
                                    selected_folder = tokens[0]
                                    allow_write = True
                                
                    elif command == "EXAMINE":
                        if not authenticated_user:
                            response = f"{tag} NO [AUTHENTICATIONFAILED] Not authenticated\r\n"
                        else:
                            if len(tokens) != 1:
                                response = f"{tag} BAD Invalid mailbox name\r\n"
                            else:
                                response = await self._handle_examine(tag, tokens[0], authenticated_user)
                                if response.startswith(f"{tag} OK"):
                                    selected_folder = tokens[0]
                                    allow_write = False
                                
                    elif command == "LIST":
                        if not authenticated_user:
                            response = f"{tag} NO [AUTHENTICATIONFAILED] Not authenticated\r\n"
                        else:
                            if len(tokens) != 2:
                                response = f"{tag} BAD Invalid LIST command format\r\n"
                            else:
                                response = self._handle_list(tag, tokens[0], tokens[1], authenticated_user)

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

    def _handle_list(self, tag: str, reference_name: str, mailbox_name: str, user: str) -> str:
        # Implementation needed
        return f'* LIST () "/" INBOX\r\n{tag} OK LIST completed\r\n'

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