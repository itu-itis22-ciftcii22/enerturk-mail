import logging
import os
from asyncio import StreamReader, StreamWriter
from typing import List
from mailbox import Maildir

class EnerturkIMAPHandler:

    def __init__(self, authenticator, mail_storage):
        self.authenticator = authenticator
        self.mail_storage = mail_storage
        self.base_dir = "mails/"

    async def _handle_client(self, reader: StreamReader, writer: StreamWriter):
        """Handle individual IMAP client connection"""
        logging.info(f"IMAP connection from {writer.get_extra_info('peername')}")

        try:
            writer.write(b"* OK [CAPABILITY IMAP4rev1] Simple IMAP Server Ready\r\n")
            await writer.drain()
            
            authenticated_user = None
            selected_folder = None
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
                    parts = data.split(' ', 2)
                    if len(parts) < 2:
                        writer.write(b"* BAD Invalid command format\r\n")
                        await writer.drain()
                        continue
                    
                    tag = parts[0]
                    command = parts[1].upper()
                    args = parts[2] if len(parts) > 2 else ""
                    
                    response = ""
                    
                    # Phase 1: Critical Commands
                    if command == "CAPABILITY":
                        response = self._handle_capability(tag)
                        
                    elif command == "LOGIN":
                        response = self._handle_login(tag, args)
                        if "OK" in response:
                            # Parse username from LOGIN args (handle quoted strings)
                            login_parts = self._parse_login_args(args)
                            if login_parts:
                                authenticated_user = login_parts[0]
                                
                    elif command == "LOGOUT":
                        response = f"* BYE IMAP4rev1 Server logging out\r\n{tag} OK LOGOUT completed\r\n"
                        writer.write(response.encode('utf-8'))
                        await writer.drain()
                        return
                        
                    elif command == "SELECT":
                        if not authenticated_user:
                            response = f"{tag} NO [AUTHENTICATIONFAILED] Not authenticated\r\n"
                        else:
                            response = self._handle_select(tag, args, authenticated_user)
                            if "OK" in response:
                                selected_folder = self._parse_mailbox_name(args)
                                
                    elif command == "EXAMINE":
                        if not authenticated_user:
                            response = f"{tag} NO [AUTHENTICATIONFAILED] Not authenticated\r\n"
                        else:
                            response = self._handle_examine(tag, args, authenticated_user)
                            if "OK" in response:
                                selected_folder = self._parse_mailbox_name(args)
                                
                    elif command == "LIST":
                        if not authenticated_user:
                            response = f"{tag} NO [AUTHENTICATIONFAILED] Not authenticated\r\n"
                        else:
                            response = self._handle_list(tag, args, authenticated_user)
                            
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
                                    response = self._handle_uid_store(tag, args, authenticated_user, selected_folder)
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


    def _parse_login_args(self, args: str) -> list[str] | None:
        """Parse LOGIN command arguments handling quoted strings"""
        parts: List[str] = []
        i = 0
        args = args.strip()
        
        while i < len(args):
            # Skip whitespace
            while i < len(args) and args[i] == ' ':
                i += 1
            
            if i >= len(args):
                break
                
            if args[i] == '"':
                # Quoted string
                part, new_i = self._parse_quoted_string(args, i)
                if part is None:
                    return None  # Malformed quoted string
                parts.append(part)
                i = new_i
            else:
                # Unquoted string
                part, new_i = self._parse_string(args, i)
                parts.append(part)
                i = new_i
        
        return parts if len(parts) >= 2 else None

    def _parse_quoted_string(self, args: str, start: int) -> tuple[str | None, int]:
        """Parse a quoted string starting at position start"""
        if args[start] != '"':
            return None, start
        
        result = ""
        i = start + 1  # Skip opening quote
        
        while i < len(args):
            char = args[i]
            if char == '"':
                # End of quoted string
                return result, i + 1
            elif char == '\\' and i + 1 < len(args):
                # Escaped character
                next_char = args[i + 1]
                if next_char in '"\\':
                    result += next_char
                    i += 2
                else:
                    # Invalid escape sequence
                    return None, i
            else:
                result += char
                i += 1
        
        # Unclosed quote
        return None, i

    def _parse_string(self, args: str, start: int) -> tuple[str, int]:
        """Parse an unquoted string (no spaces, quotes, or special chars)"""
        result = ""
        i = start
        
        while i < len(args) and args[i] not in ' "(){}[]':
            result += args[i]
            i += 1
        
        return result, i

    def _parse_mailbox_name(self, args : str) -> str | None:
        """Parse mailbox name from command arguments"""
        args = args.strip()
        if not args:
            return "INBOX"
        elif args.startswith('"') and args.endswith('"'):
            return args[1:-1]
        elif len(args.split()) == 1:
            return args
        else:
            return None

    # Placeholder methods for command handlers
    def _handle_capability(self, tag : str) -> str:
        # Only advertise what's actually implemented
        return f"* CAPABILITY IMAP4rev1\r\n{tag} OK CAPABILITY completed\r\n"
    
    def _authenticate_user(self, username: str, password: str) -> bool:
        """Placeholder for user authentication logic"""
        # In a real implementation, this would check against a database or other storage
        return username == "testuser" and password == "testpass"
    
    def _handle_login(self, tag: str, args: str) -> str:
        parsed_args = self._parse_login_args(args)
        
        if not parsed_args:
            return f"{tag} BAD LOGIN command requires username and password\r\n"
        
        username, password = parsed_args[0], parsed_args[1]
        
        # Authenticate user
        if self._authenticate_user(username, password):
            return f"{tag} OK LOGIN completed\r\n"
        else:
            return f"{tag} NO [AUTHENTICATIONFAILED] Invalid credentials\r\n"
        
    def handle_select(self, tag: str, args: str, user: str) -> str:
        mailbox_name = self._parse_mailbox_name(args)
        if not mailbox_name:
            return f"{tag} BAD Invalid mailbox name\r\n"
        
        dirname = os.path.join(self.base_dir, user, mailbox_name)

        if not os.path.isdir(dirname):
            return f"{tag} NO [NONMAILBOX] Not a mailbox directory\r\n"
        
        mailbox = Maildir(dirname, create=True)
        keys = mailbox.keys()
        mailbox_recent = Maildir(dirname + "/new", create=True)
        keys_recent = mailbox_recent.keys()
        unseen_count = sum(1 for key in keys if "S" not in mailbox.get_flags(key))


        response = f"* {len(keys)} EXISTS\r\n"
        response += f"* {len(keys_recent)} RECENT\r\n"
        response += f"* OK [UNSEEN {unseen_count}] First unseen\r\n"
        response += f"* FLAGS (\\Answered \\Flagged \\Deleted \\Seen \\Draft)\r\n"
        response += f"* OK [PERMANENTFLAGS (\\Deleted \\Seen \\*)] Limited\r\n"
        response += f"{tag} OK [READ-WRITE] SELECT completed\r\n"

    def _handle_select(self, tag: str, args: str, user: str) -> str:
        # Implementation needed
        return f"* 0 EXISTS\r\n* 0 RECENT\r\n* OK [UIDVALIDITY 1] UIDs valid\r\n* OK [UIDNEXT 1] Predicted next UID\r\n{tag} OK [READ-WRITE] SELECT completed\r\n"

    def _handle_examine(self, tag: str, args: str, user: str) -> str:
        # Implementation needed (like SELECT but read-only)
        return f"* 0 EXISTS\r\n* 0 RECENT\r\n* OK [UIDVALIDITY 1] UIDs valid\r\n* OK [UIDNEXT 1] Predicted next UID\r\n{tag} OK [READ-ONLY] EXAMINE completed\r\n"

    def _handle_list(self, tag: str, args: str, user: str) -> str:
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