import logging
import os
import asyncio
import base64
import ssl
import shlex
from typing import List, Tuple, Optional, Union
from server.storage_manager import MaildirWrapper
from server.imap_fetcher import Fetcher
from mailbox import MaildirMessage
from server.authenticator import LDAPAuthenticator

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

class IMAPContext:
    """Context object to hold IMAP session state"""
    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        self.authenticated_user: Optional[str] = None
        self.selected_folder: Optional[str] = None
        self.read_only: bool = True
        self.tls_active: bool = False

class FetchProcessor:
    """Handles FETCH command processing"""
    
    def __init__(self):
        self.fetcher = Fetcher()
    
    async def handle_seq_fetch(self, tag: str, sequences: str, item_names: str, context: IMAPContext) -> str:
        """Handle sequence-based FETCH command"""
        mailbox = self._get_mailbox(context)
        message_pairs = await self._get_message_uid_key_pairs(mailbox)
        
        if not message_pairs:
            return f"{tag} OK FETCH completed (no messages)\r\n"
        
        try:
            seq_list = self._parse_sequence_set(sequences, len(message_pairs))
            if isinstance(seq_list, str):  # Error message
                return f"{tag} BAD {seq_list}\r\n"
                
            fetch_targets = self._get_targets_from_seq_list(seq_list, message_pairs)
            return await self._handle_fetch_command(tag, fetch_targets, item_names, mailbox, False)
        except Exception as e:
            logging.error(f"Error processing sequence FETCH: {e}")
            return f"{tag} BAD Error processing FETCH command\r\n"
    
    async def handle_uid_fetch(self, tag: str, uids: str, item_names: str, context: IMAPContext) -> str:
        """Handle UID-based FETCH command"""
        mailbox = self._get_mailbox(context)
        message_pairs = await self._get_message_uid_key_pairs(mailbox)
        
        if not message_pairs:
            return f"{tag} OK UID FETCH completed (no messages)\r\n"
        
        try:
            uid_list = await self._parse_uid_set(uids, mailbox)
            if isinstance(uid_list, str):  # Error message
                return f"{tag} BAD {uid_list}\r\n"
                
            fetch_targets = await self._get_targets_from_uid_list(uid_list, mailbox, message_pairs)
            return await self._handle_fetch_command(tag, fetch_targets, item_names, mailbox, True)
        except Exception as e:
            logging.error(f"Error processing UID FETCH: {e}")
            return f"{tag} BAD Error processing UID FETCH command\r\n"
    
    async def _get_message_uid_key_pairs(self, mailbox: MaildirWrapper) -> List[Tuple[int, str]]:
        """Get sorted list of (uid, key) pairs for all messages in mailbox"""
        message_keys = mailbox.get_keys_safe()
        message_pairs: List[Tuple[int, str]] = []

        for key in message_keys:
            uid = await mailbox.get_uid_from_key(key)
            if uid is not None:
                message_pairs.append((uid, key))
        
        return sorted(message_pairs, key=lambda pair: pair[0])
    
    def _parse_sequence_set(self, sequences: str, max_seq: int) -> Union[List[int], str]:
        """Parse sequence set into list of sequence numbers"""
        seq_list: List[int] = []
        
        try:
            for seq_part in sequences.split(','):
                seq_part = seq_part.strip()
                
                if ':' in seq_part:
                    # Handle range (e.g., "1:5", "1:*")
                    start_str, end_str = seq_part.split(':')
                    start_seq = int(start_str) if start_str != '*' else max_seq
                    end_seq = int(end_str) if end_str != '*' else max_seq
                    
                    # Ensure within valid range
                    start_seq = max(1, min(start_seq, max_seq))
                    end_seq = max(1, min(end_seq, max_seq))
                    
                    if start_seq <= end_seq:
                        seq_list.extend(range(start_seq, end_seq + 1))
                else:
                    # Handle single sequence number
                    if seq_part == '*':
                        seq_list.append(max_seq)
                    else:
                        seq_num = int(seq_part)
                        if 1 <= seq_num <= max_seq:
                            seq_list.append(seq_num)
        except ValueError:
            return "Invalid sequence set"
        
        return sorted(set(seq_list))
    
    async def _parse_uid_set(self, uids: str, mailbox: MaildirWrapper) -> Union[List[int], str]:
        """Parse UID set into list of UIDs"""
        uid_list: List[int] = []
        max_uid = await mailbox.get_uidnext() - 1
        has_messages = len(mailbox.get_keys_safe()) > 0
        
        try:
            for uid_part in uids.split(','):
                uid_part = uid_part.strip()
                
                if ':' in uid_part:
                    # Handle range (e.g., "1:5", "1:*")
                    start_str, end_str = uid_part.split(':')
                    
                    # Handle special case for "*"
                    if start_str == "*":
                        start_uid = max_uid if has_messages else 0
                    else:
                        start_uid = int(start_str)
                        
                    if end_str == "*":
                        end_uid = max_uid
                    else:
                        end_uid = int(end_str)
                    
                    # For UID ranges, we include UIDs that may not exist
                    if start_uid <= end_uid:
                        uid_list.extend(range(start_uid, end_uid + 1))
                elif uid_part == '*':
                    uid_list.append(max_uid if has_messages else 0)
                else:
                    uid_list.append(int(uid_part))
        except ValueError:
            return "Invalid UID set"
        
        return sorted(set(uid_list))
    
    def _get_targets_from_seq_list(self, seq_list: List[int], message_pairs: List[Tuple[int, str]]) -> List[Tuple[int, int, str]]:
        """Convert sequence numbers to fetch targets"""
        fetch_targets: List[Tuple[int, int, str]] = []
        
        for seq in seq_list:
            if 1 <= seq <= len(message_pairs):
                index = seq - 1
                uid, key = message_pairs[index]
                fetch_targets.append((seq, uid, key))
        
        return fetch_targets
    
    async def _get_targets_from_uid_list(self, uid_list: List[int], mailbox: MaildirWrapper, 
                                        message_pairs: List[Tuple[int, str]]) -> List[Tuple[int, int, str]]:
        """Convert UIDs to fetch targets"""
        # Create mapping from UID to sequence number
        uid_to_seq = {uid: seq for seq, (uid, _) in enumerate(message_pairs, 1)}

        fetch_targets: List[Tuple[int, int, str]] = []
        for uid in uid_list:
            key = await mailbox.get_key_from_uid(uid)
            if key is not None:
                seq_num = uid_to_seq.get(uid, -1)
                fetch_targets.append((seq_num, uid, key))
        
        return fetch_targets
    
    async def _handle_fetch_command(self, tag: str, fetch_targets: List[Tuple[int, int, str]], 
                                  item_names: str, mailbox: MaildirWrapper, is_uid_fetch: bool) -> str:
        """Handle complete FETCH processing"""
        try:
            items = self.fetcher.parse_fetch_items(item_names)
        except Exception as e:
            logging.error(f"Failed to parse fetch items: {e}")
            return f"{tag} BAD Invalid fetch items\r\n"
        
        # Macro expansions
        MACROS = {
            'ALL': ['FLAGS', 'INTERNALDATE', 'RFC822.SIZE', 'ENVELOPE'],
            'FAST': ['FLAGS', 'INTERNALDATE', 'RFC822.SIZE'],
            'FULL': ['FLAGS', 'INTERNALDATE', 'RFC822.SIZE', 'ENVELOPE', 'BODY']
        }
        
        if len(items) == 1 and items[0].upper() in MACROS:
            items = MACROS[items[0].upper()]
        
        command_name = "UID FETCH" if is_uid_fetch else "FETCH"
        response = ""
        
        for seq_num, uid, key in fetch_targets:
            try:
                message = mailbox.get_message_safe(key)
                if message:
                    fetch_response = await self._handle_fetch_message(
                        seq_num, uid, key, message, items, is_uid_fetch)
                    if fetch_response:
                        response += fetch_response
            except Exception as e:
                logging.warning(f"Error processing {command_name} for seq={seq_num}, uid={uid}: {e}")
                continue
        
        response += f"{tag} OK {command_name} completed\r\n"
        return response
    
    async def _handle_fetch_message(self, seq_num: int, uid: int, key: str, 
                                  message: MaildirMessage, items: List[str], is_uid_fetch: bool) -> str:
        """Handle FETCH for a single message"""
        fetch_items: List[str] = []
        
        for item in items:
            try:
                upper = item.upper()
                if upper == 'UID':
                    fetch_items.append(f'{item} {uid}')
                else:
                    result = self.fetcher.handle_fetch_item(item, message)
                    if result:  # Only add if the item is implemented
                        fetch_items.append(result)
            except Exception as e:
                logging.warning(f"Error handling fetch item {item}: {e}")
                continue
        
        if not fetch_items:
            return ""
        
        # Always include UID in UID FETCH responses (IMAP requirement)
        if is_uid_fetch and not any(item.upper().startswith('UID ') for item in fetch_items):
            fetch_items.insert(0, f'UID {uid}')
        
        return self._format_fetch_response(seq_num, fetch_items)
    
    def _format_fetch_response(self, seq_num: int, fetch_items: List[str]) -> str:
        """Format FETCH response with all data as quoted strings"""
        if not fetch_items:
            return ""
            
        return f"* {seq_num} FETCH ({' '.join(fetch_items)} )\r\n"
    
    def _get_mailbox(self, context: IMAPContext) -> MaildirWrapper:
        """Get mailbox wrapper for current context"""
        if not context.authenticated_user:
            raise ValueError("Not authenticated")
            
        base_path = os.path.join(context.base_dir, context.authenticated_user)
        folder_name = "" if context.selected_folder == "INBOX" else context.selected_folder
        
        return MaildirWrapper(base_path, folder_name=folder_name, create=False)

class IMAPHandler:
    """Refactored IMAP handler with integrated command handlers"""
    
    def __init__(self, base_dir: str, host_name: str, ssl_context: ssl.SSLContext, auth_type: str):
        self.base_dir = base_dir
        self.host_name = host_name
        self.ssl_context = ssl_context
        self.fetch_processor = FetchProcessor()
        self.auth_type = auth_type
        self.authenticator = LDAPAuthenticator(self.auth_type)

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Handle individual IMAP client connection"""
        logging.info(f"IMAP connection from {writer.get_extra_info('peername')}")
        
        context = IMAPContext(self.base_dir)
        
        try:
            await self._send_greeting(writer)
            
            while True:
                command_line = await self._read_command(reader, writer)
                if command_line is None:
                    break
                
                tag, command, args = self._parse_command(command_line)
                if tag is None or command is None:
                    await self._send_response(writer, "* BAD Invalid command format\r\n")
                    continue
                
                response = await self._handle_command(tag, command, args, context, reader, writer)
                if response:
                    await self._send_response(writer, response)
                
                if command == "LOGOUT":
                    break

        except ConnectionResetError:
            logging.info("IMAP client disconnected")
        except Exception as e:
            logging.error(f"IMAP client error: {e}")
            await self._send_error_response(writer)
        finally:
            await self._cleanup_connection(writer)

    async def _send_greeting(self, writer: asyncio.StreamWriter):
        """Send initial greeting to client"""
        greeting = "* OK Simple IMAP Server Ready\r\n"
        await self._send_response(writer, greeting)

    async def _read_command(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> Optional[str]:
        """Read and decode command from client"""
        try:
            line = await reader.readuntil("\r\n".encode('ascii'))
            if not line:
                return None
            command_line = line.decode('ascii')
            logging.debug(f"IMAP << {command_line.encode('ascii')}")
            return command_line
        except UnicodeDecodeError:
            await self._send_response(writer, "* BAD Command line is not valid ASCII\r\n")
            return None

    def _parse_command(self, command_line: str) -> Tuple[Optional[str], Optional[str], str]:
        """Parse command line into tag, command, and args"""
        parts = command_line.rstrip("\r\n").split(" ", 2)
        if len(parts) < 2:
            return None, None, ""
        
        tag = parts[0]
        command = parts[1].upper()
        args = parts[2] if len(parts) > 2 else ""
        
        return tag, command, args

    async def _handle_command(self, tag: str, command: str, args: str, 
                            context: IMAPContext, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> str:
        """Route command to appropriate handler"""
        
        # Handle special commands that need reader/writer access
        if command == "STARTTLS":
            return await self._handle_starttls(tag, context, reader, writer)
        elif command == "AUTHENTICATE":
            return await self._handle_authenticate(tag, args, context, reader, writer)
        elif command == "LOGIN":
            return await self._handle_authenticate(tag, "PLAIN " + args, context, reader, writer)
        elif command == "LOGOUT":
            return await self._handle_logout(tag, writer)
        
        # Use method dispatch for other commands
        handler_method = getattr(self, f"_handle_{command.lower()}", None)
        if handler_method:
            return await handler_method(tag, args, context)
        else:
            return f"{tag} BAD Command '{command}' not recognized\r\n"

    async def _handle_capability(self, tag: str, args: str, context: IMAPContext) -> str:
        capabilities = ["IMAP4rev1", "AUTH=PLAIN", "LOGINDISABLED", "STARTTLS"]
        capability_str = " ".join(capabilities)
        return f"* CAPABILITY {capability_str}\r\n{tag} OK CAPABILITY completed\r\n"

    async def _handle_select(self, tag: str, args: str, context: IMAPContext) -> str:
        if not context.authenticated_user:
            return f"{tag} NO Not authenticated\r\n"
        
        lexer = shlex.split(args)
        if len(lexer) != 1:
            return f"{tag} BAD Invalid SELECT command format\r\n"
        
        mailbox_name = lexer[0]
        base_mailbox_path = os.path.join(context.base_dir, context.authenticated_user)
        
        try:
            if mailbox_name.upper() == 'INBOX':
                mailbox = MaildirWrapper(base_mailbox_path, create=False)
            else:
                mailbox = MaildirWrapper(base_mailbox_path, folder_name=mailbox_name, create=False)
        except FileNotFoundError:
            return f"{tag} NO [NONMAILBOX] Mailbox does not exist\r\n"

        try:
            exists, recent, first_unseen, uidvalidity, uidnext = await asyncio.gather(
                mailbox.get_message_count(),
                mailbox.get_recent_count(),
                mailbox.get_first_unseen_seq(),
                mailbox.get_uidvalidity(),
                mailbox.get_uidnext()
            )

            response = f"* {exists} EXISTS\r\n"
            response += f"* {recent} RECENT\r\n"

            if first_unseen is not None:
                response += f"* OK [UNSEEN {first_unseen}] Message {first_unseen} is first unseen\r\n"

            response += f"* FLAGS (\\Answered \\Flagged \\Deleted \\Seen \\Draft)\r\n"
            response += f"* OK [PERMANENTFLAGS (\\Deleted \\Seen)] Limited\r\n"
            response += f"* OK [UIDVALIDITY {uidvalidity}] UIDs valid\r\n"
            response += f"* OK [UIDNEXT {uidnext}] Predicted next UID\r\n"
            response += f"{tag} OK [READ-WRITE] SELECT completed\r\n"
            
            context.selected_folder = mailbox_name
            context.read_only = False
            
            return response

        except Exception as e:
            return f"{tag} NO [SERVERFAILURE] Server error: {str(e)}\r\n"

    async def _handle_examine(self, tag: str, args: str, context: IMAPContext) -> str:
        response = await self._handle_select(tag, args, context)
        response = response.replace("SELECT", "EXAMINE")
        response = response.replace("[READ-WRITE]", "[READ-ONLY]")
        context.read_only = True
        return response

    async def _handle_list(self, tag: str, args: str, context: IMAPContext) -> str:
        if not context.authenticated_user:
            return f"{tag} NO Not authenticated\r\n"
        
        lexer = shlex.split(args)
        if len(lexer) != 2:
            return f"{tag} BAD Invalid LIST command format\r\n"
        
        reference_name, mailbox_name = lexer
        return await self._handle_list_internal(tag, reference_name, mailbox_name, context.authenticated_user, context.base_dir)

    async def _handle_list_internal(self, tag: str, reference_name: str, mailbox_name: str, user: str, base_dir: str) -> str:
        if ".." in reference_name or ".." in mailbox_name:
            return f"{tag} NO Invalid reference name\r\n"

        base_mailbox_path = os.path.join(base_dir, user)

        if mailbox_name == "":
            response = '* LIST (\\Noselect) "/" ""\r\n'
            return f'{response}{tag} OK LIST completed\r\n'
        elif mailbox_name.startswith("/"):
            search_pattern = mailbox_name[1:]
        else:
            search_pattern = reference_name + mailbox_name

        search_pattern = search_pattern.lstrip("/")
        response = ""

        if search_pattern.endswith("*") or search_pattern.endswith("%"):
            prefix = search_pattern[:-1]
            
            try:
                if "INBOX".startswith(prefix):
                    inbox_mailbox = MaildirWrapper(base_mailbox_path, folder_name="", create=False)
                    attributes = await inbox_mailbox.get_folder_attributes()
                    attr_str = " ".join(attributes)
                    response += f'* LIST ({attr_str}) "/" "INBOX"\r\n'
                
                root_mailbox = MaildirWrapper(base_mailbox_path, folder_name="", create=False)
                relative_folder_names = root_mailbox.list_folders_safe()
                
                for relative_folder_name in relative_folder_names:
                    if relative_folder_name.startswith(prefix):
                        try:
                            submailbox = MaildirWrapper(base_mailbox_path, folder_name=relative_folder_name, create=False)
                            attributes = await submailbox.get_folder_attributes()
                            attr_str = " ".join(attributes)
                            response += f'* LIST ({attr_str}) "/" "{relative_folder_name}"\r\n'
                        except FileNotFoundError:
                            logging.warning(f"Invalid mailbox directory: {relative_folder_name}")
                            continue
                            
            except FileNotFoundError:
                return f"{tag} NO [NONMAILBOX] Not a mailbox directory\r\n"

        else:
            try:
                if search_pattern == "INBOX":
                    mailbox = MaildirWrapper(base_mailbox_path, folder_name="", create=False)
                else:
                    mailbox = MaildirWrapper(base_mailbox_path, folder_name=search_pattern, create=False)
                    
                attributes = await mailbox.get_folder_attributes()
                attr_str = " ".join(attributes)
                response += f'* LIST ({attr_str}) "/" "{search_pattern}"\r\n'
                
            except FileNotFoundError:
                pass

        return f'{response}{tag} OK LIST completed\r\n'

    async def _handle_lsub(self, tag: str, args: str, context: IMAPContext) -> str:
        if not context.authenticated_user:
            return f"{tag} NO [AUTHENTICATIONFAILED] Not authenticated\r\n"
        
        response = await self._handle_list(tag, args, context)
        return response.replace("LIST", "LSUB")

    async def _handle_status(self, tag: str, args: str, context: IMAPContext) -> str:
        if not context.authenticated_user:
            return f"{tag} NO [AUTHENTICATIONFAILED] Not authenticated\r\n"
        
        args_parts = args.split(" ", 1)
        if len(args_parts) < 2:
            return f"{tag} BAD Invalid STATUS command format\r\n"
        
        mailbox_name = args_parts[0]
        item_names = args_parts[1]
        
        if mailbox_name.startswith('"') and mailbox_name.endswith('"'):
            mailbox_name = mailbox_name[1:-1]
        
        if item_names.startswith('(') and item_names.endswith(')'):
            item_names = item_names[1:-1]
        
        items = item_names.split()
        base_path = os.path.join(context.base_dir, context.authenticated_user)
        
        if mailbox_name.upper() == 'INBOX':
            folder = ""
        else:
            folder = mailbox_name
        
        try:
            wrapper = MaildirWrapper(base_path, folder_name=folder, create=False)
        except FileNotFoundError:
            return f"{tag} NO Mailbox does not exist\r\n"
        
        parts: List[str] = []
        for item in items:
            key = item.upper()
            if key == 'MESSAGES':
                cnt = await wrapper.get_message_count()
                parts.append(f"MESSAGES {cnt}")
            elif key == 'RECENT':
                cnt = await wrapper.get_recent_count()
                parts.append(f"RECENT {cnt}")
            elif key == 'UIDNEXT':
                u = await wrapper.get_uidnext()
                parts.append(f"UIDNEXT {u}")
            elif key == 'UIDVALIDITY':
                uv = await wrapper.get_uidvalidity()
                parts.append(f"UIDVALIDITY {uv}")
            elif key == 'UNSEEN':
                def count_unseen():
                    total = 0
                    for k in wrapper.get_keys_safe():
                        msg = wrapper.get_message_safe(k)
                        if msg and 'S' not in msg.get_flags():
                            total += 1
                    return total
                unseen = await asyncio.to_thread(count_unseen)
                parts.append(f"UNSEEN {unseen}")
        
        attr_str = ' '.join(parts)
        return f"* STATUS {mailbox_name} ({attr_str})\r\n{tag} OK STATUS completed\r\n"

    async def _handle_fetch(self, tag: str, args: str, context: IMAPContext, is_uid: bool = False) -> str:
        if not context.authenticated_user:
            return f"{tag} NO [AUTHENTICATIONFAILED] Not authenticated\r\n"
        elif not context.selected_folder:
            return f"{tag} NO [CLIENTBUG] No folder selected\r\n"
        
        args_parts = args.split(" ", 1)
        if len(args_parts) < 2:
            return f"{tag} BAD Invalid FETCH command format\r\n"
        
        sequences = args_parts[0]
        item_names = args_parts[1]
        
        if is_uid:
            return await self.fetch_processor.handle_uid_fetch(tag, sequences, item_names, context)
        else:
            return await self.fetch_processor.handle_seq_fetch(tag, sequences, item_names, context)

    async def _handle_uid(self, tag: str, args: str, context: IMAPContext) -> str:
        if not context.authenticated_user:
            return f"{tag} NO Not authenticated\r\n"
        elif not context.selected_folder:
            return f"{tag} NO No folder selected\r\n"
        
        args_parts = args.split(" ", 1)
        if len(args_parts) < 2:
            return f"{tag} BAD Invalid UID command format\r\n"
        
        command = args_parts[0].upper()
        command_args = args_parts[1]
        
        if command == "FETCH":
            return await self._handle_fetch(tag, command_args, context, is_uid=True)
        else:
            return f"{tag} BAD UID subcommand '{command}' not recognized\r\n"

    async def _handle_close(self, tag: str, args: str, context: IMAPContext) -> str:
        if not context.authenticated_user:
            return f"{tag} NO Not authenticated\r\n"
        elif not context.selected_folder:
            return f"{tag} NO No folder selected\r\n"
        
        context.selected_folder = None
        return f"{tag} OK CLOSE completed, now in authenticated state\r\n"

    async def _handle_noop(self, tag: str, args: str, context: IMAPContext) -> str:
        return f"{tag} OK NOOP completed\r\n"

    async def _handle_starttls(self, tag: str, context: IMAPContext, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> str:
        """Handle STARTTLS command"""
        if context.tls_active:
            return f"{tag} BAD TLS already active\r\n"
        elif context.authenticated_user:
            return f"{tag} BAD Cannot start TLS after authentication\r\n"
        else:
            response = f"{tag} OK Begin TLS negotiation now\r\n"
            await self._send_response(writer, response)
            await writer.start_tls(self.ssl_context)
            context.tls_active = True
            return ""

    async def _handle_authenticate(self, tag: str, args: str, context: IMAPContext, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> str:
        """Handle AUTHENTICATE command"""
        if context.authenticated_user:
            return f"{tag} NO Already authenticated\r\n"
        elif args != "PLAIN":
            return f"{tag} NO Unsupported authentication mechanism\r\n"
        
        # Send continuation prompt
        await self._send_response(writer, "+\r\n")
        
        try:
            credentials = await reader.readuntil("\r\n".encode('ascii'))
            logging.debug(f"IMAP << {credentials.decode('ascii')}")
            logging.debug(f"Received credentials line: {credentials!r}")
        except asyncio.IncompleteReadError:
            return f"{tag} BAD Incomplete credentials\r\n"
        
        try:
            credentials = credentials.rstrip(b"\r\n")
            credentials = base64.b64decode(credentials)
            credential_parts = credentials.split(b'\x00', 2)
            if len(credential_parts) != 3:
                raise ValueError
            credential_parts = [part.decode('utf-8') for part in credential_parts]
        except Exception:
            return f"{tag} BAD Invalid PLAIN credentials format\r\n"
        
        authzid, authcid, password = credential_parts
        logging.debug(f"authzid:{authzid} authcid:{authcid} password:{password}\r\n")
        
        if self.authenticator.authenticate_user(authcid, password):
            context.authenticated_user = authcid.rstrip('@' + self.host_name)
            return f"{tag} OK AUTHENTICATE completed\r\n"
        else:
            return f"{tag} NO Invalid credentials\r\n"

    async def _handle_logout(self, tag: str, writer: asyncio.StreamWriter) -> str:
        """Handle LOGOUT command"""
        response = f"* BYE IMAP4rev1 Server logging out\r\n{tag} OK LOGOUT completed\r\n"
        await self._send_response(writer, response)
        return ""

    async def _send_response(self, writer: asyncio.StreamWriter, response: str):
        """Send response to client"""
        response_bytes = response.encode('ascii')
        writer.write(response_bytes)
        await writer.drain()
        logging.debug(f"IMAP >> {response_bytes}")

    async def _send_error_response(self, writer: asyncio.StreamWriter):
        """Send error response to client"""
        farewell = "* BYE Server error, closing connection\r\n"
        try:
            await self._send_response(writer, farewell)
        except Exception as send_err:
            logging.error(f"Failed to send BYE: {send_err}")

    async def _cleanup_connection(self, writer: asyncio.StreamWriter):
        """Clean up connection"""
        try:
            writer.close()
            await writer.wait_closed()
        except Exception as close_err:
            logging.error(f"Failed to close writer: {close_err}")