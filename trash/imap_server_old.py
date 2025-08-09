import logging
import os
import asyncio
import base64
# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
from typing import List, Tuple
from server.storage_manager import MaildirWrapper
from imap_fetcher import Fetcher
from mailbox import MaildirMessage
import ssl
import shlex
from config import HOST_NAME, USERNAME, PASSWORD

class EnerturkIMAPHandler:

    def __init__(self, base_dir: str, ssl_context: ssl.SSLContext):
        self.base_dir = base_dir
        self.ssl_context = ssl_context
        self.users = {USERNAME + '@' + HOST_NAME: PASSWORD}  # Placeholder user credentials

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Handle individual IMAP client connection"""
        logging.info(f"IMAP connection from {writer.get_extra_info('peername')}")

        response : str = ""

        try:
            greeting = "* OK Simple IMAP Server Ready\r\n"
            writer.write(greeting.encode('ascii'))
            await writer.drain()
            logging.debug(f"IMAP >> {greeting.encode('ascii')}")

            authenticated_user = None
            selected_folder = None
            tls_active = False
            read_only = True
            
            while True:
                line = await reader.readuntil("\r\n".encode('ascii'))
                if not line:
                    break
                    
                try:
                    command_line = line.decode('ascii')
                except:
                    warning = "* BAD Command line is not valid ASCII\r\n"
                    writer.write(warning.encode('ascii'))
                    await writer.drain()
                    logging.debug(f"IMAP >> {warning.encode('ascii')}")
                    continue

                logging.debug(f"IMAP << {command_line.encode('ascii')}")

                # Parse command
                command_line = command_line.rstrip("\r\n")
                command_line = command_line.split(" ", 2)

                if len(command_line) < 2:
                    response = "* BAD Invalid command format\r\n"
                    writer.write(response.encode('ascii'))
                    await writer.drain()
                    logging.debug(f"IMAP >> {response.encode('ascii')}")
                    continue
                else:
                    tag = command_line[0]
                    command = command_line[1].upper()
                    args = command_line[2] if len(command_line) > 2 else ""
                
                response : str = ""

                if command == "CAPABILITY":
                    response = self._handle_capability(tag)

                elif command == "STARTTLS":
                    if tls_active:
                        response = f"{tag} BAD TLS already active\r\n"
                    elif authenticated_user:
                        response = f"{tag} BAD Cannot start TLS after authentication\r\n"
                    else:
                        response = f"{tag} OK Begin TLS negotiation now\r\n"
                        writer.write(response.encode('ascii'))
                        await writer.drain()
                        logging.debug(f"IMAP >> {response.encode('ascii')}")
                        await writer.start_tls(self.ssl_context)
                        tls_active = True  # Update TLS state
                        continue

                elif command == "AUTHENTICATE":
                    if authenticated_user:
                        response = f"{tag} NO Already authenticated\r\n"
                    elif args != "PLAIN":
                        response = f"{tag} NO Unsupported authentication mechanism\r\n"
                    else:
                        # Handle PLAIN authentication
                        prompt = "+\r\n"
                        writer.write(prompt.encode('ascii'))  # Prompt for credentials
                        await writer.drain()
                        logging.debug(f"IMAP >> {prompt.encode('ascii')}")
                        try:
                            credentials = await reader.readuntil("\r\n".encode('ascii'))
                            logging.debug(f"IMAP << {credentials.decode('ascii')}")
                        except asyncio.IncompleteReadError:
                            response = f"{tag} BAD Incomplete credentials\r\n"
                            writer.write(response.encode('ascii'))
                            await writer.drain()
                            logging.debug(f"IMAP >> {response.encode('ascii')}")
                            continue
                        try:
                            credentials = credentials.rstrip(b"\r\n")
                            credentials = base64.b64decode(credentials)
                            credential_parts = credentials.split(b'\x00', 2)
                            if len(credential_parts) != 3:
                                raise ValueError
                            credential_parts = [part.decode('utf-8') for part in credential_parts]
                        except Exception:
                            response = f"{tag} BAD Invalid PLAIN credentials format\r\n"
                            writer.write(response.encode('ascii'))
                            await writer.drain()
                            logging.debug(f"IMAP >> {response.encode('ascii')}")
                            continue
                        authzid = credential_parts[0]
                        authcid = credential_parts[1]
                        password = credential_parts[2]
                        logging.debug(f"authzid:{authzid} authcid:{authcid} password:{password}\r\n")
                        response = await self._handle_authenticate(tag, authzid, authcid, password)
                        if "OK" in response:
                            authenticated_user = authzid.rstrip('@' + HOST_NAME) if authzid != "" else authcid.rstrip('@' + HOST_NAME)

                elif command == "LOGOUT":
                    response = f"* BYE IMAP4rev1 Server logging out\r\n{tag} OK LOGOUT completed\r\n"
                    writer.write(response.encode('ascii'))
                    await writer.drain()
                    logging.debug(f"IMAP >> {response.encode('ascii')}")
                    return
                
                elif command == "SELECT":
                    if not authenticated_user:
                        response = f"{tag} NO Not authenticated\r\n"
                    else:
                        selected_folder = None
                        read_only = True
                        lexer = shlex.split(args)
                        if len(lexer) != 1:
                            response = f"{tag} BAD Invalid SELECT command format\r\n"
                        else:
                            # Handle SELECT command with mailbox name
                            response = await self._handle_select(tag, lexer[0], authenticated_user)
                            if "OK" in response:
                                selected_folder = lexer[0]
                                read_only = False
                            
                elif command == "EXAMINE":
                    if not authenticated_user:
                        response = f"{tag} NO Not authenticated\r\n"
                    else:
                        selected_folder = None
                        read_only = True
                        lexer = shlex.split(args)
                        if len(lexer) != 1:
                            response = f"{tag} BAD Invalid EXAMINE command format\r\n"
                        else:
                            # Handle EXAMINE command with mailbox name
                            response = await self._handle_select(tag, lexer[0], authenticated_user)
                            response = response.replace("SELECT", "EXAMINE")
                            response = response.replace("[READ-WRITE]", "[READ-ONLY]")
                            if "OK" in response:
                                selected_folder = lexer[0]
                            read_only = True
                            
                elif command == "LIST":
                    if not authenticated_user:
                        response = f"{tag} NO Not authenticated\r\n"
                    else:
                        lexer = shlex.split(args)
                        if len(lexer) != 2:
                            response = f"{tag} BAD Invalid LIST command format\r\n"
                        else:
                            # Handle LIST command with reference and mailbox name
                            response = await self._handle_list(tag, lexer[0], lexer[1], authenticated_user)

                elif command == "LSUB":
                    if not authenticated_user:
                        response = f"{tag} NO [AUTHENTICATIONFAILED] Not authenticated\r\n"
                    else:
                        lexer = shlex.split(args)
                        if len(lexer) != 2:
                            response = f"{tag} BAD Invalid LSUB command format\r\n"
                        else:
                            # LSUB shows subscribed folders - for simplicity, just show same as LIST
                            response = await self._handle_list(tag, lexer[0], lexer[1], authenticated_user)
                            # Replace LIST with LSUB in the response
                            response = response.replace("LIST", "LSUB")

                elif command == "STATUS":
                    if not authenticated_user:
                        response = f"{tag} NO [AUTHENTICATIONFAILED] Not authenticated\r\n"
                    else:
                        args = args.split(" ", 1)
                        if len(args) < 2:
                            response = f"{tag} BAD Invalid STATUS command format\r\n"
                        else:
                            mailbox_name = args[0]
                            item_names = args[1]
                            response = await self._handle_status(tag, mailbox_name, item_names, authenticated_user)

                elif command == "FETCH":
                    if not authenticated_user:
                        response = f"{tag} NO [AUTHENTICATIONFAILED] Not authenticated\r\n"
                    elif not selected_folder:
                        response = f"{tag} NO [CLIENTBUG] No folder selected\r\n"
                    elif len(args) < 2:
                        response = f"{tag} BAD Invalid FETCH command format\r\n"
                    else:
                        response = await self._handle_seq_fetch(tag, args[0], " ".join(args[1:]), authenticated_user, selected_folder)
                        
                elif command == "UID":
                    if not authenticated_user:
                        response = f"{tag} NO Not authenticated\r\n"
                    elif not selected_folder:
                        response = f"{tag} NO No folder selected\r\n"
                    else:
                        args = args.split(" ", 1)
                        if len(args) < 2:
                            response = f"{tag} BAD Invalid UID command format\r\n"
                        else:
                            command = args[0].upper()
                            args = args[1]
                            if command == "FETCH":
                                args = args.split(" ", 1)
                                if len(args) < 2:
                                    response = f"{tag} BAD Invalid UID FETCH command format\r\n"
                                else:
                                    uids = args[0]
                                    item_names = args[1]
                                    response = await self._handle_uid_fetch(tag, uids, item_names, authenticated_user, selected_folder)
                            else:
                                response = f"{tag} BAD UID subcommand '{command}' not recognized\r\n"
                        
                elif command == "CLOSE":
                    if not authenticated_user:
                        response = f"{tag} NO Not authenticated\r\n"
                    elif not selected_folder:
                        response = f"{tag} NO No folder selected\r\n"
                    else:
                        response = f"{tag} OK - close completed, now in authenticated state"
                        if not read_only:
                            # If not read-only, save changes to the folder
                            pass
                        selected_folder = None  # Return to authenticated state

                elif command == "NOOP":
                    response = f"{tag} OK NOOP completed\r\n"

                else:
                    response = f"{tag} BAD Command '{command}' not recognized\r\n"

                
                # Send response
                if response:
                    writer.write(response.encode('ascii'))
                    await writer.drain()
                    logging.debug(f"IMAP >> {response.encode('ascii')}")

        except ConnectionResetError:
            logging.info("IMAP client disconnected")
        except Exception as e:
            logging.error(f"IMAP client error: {e}")
            if response:
                logging.error(f"IMAP response before error: {response}")
            farewell = "* BYE Server error, closing connection\r\n"
            try:
                writer.write(farewell.encode('ascii'))
                await writer.drain()
                logging.debug(f"IMAP >> {farewell.encode('ascii')}")
            except Exception as send_err:
                logging.error(f"Failed to send BYE: {send_err}")
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception as close_err:
                logging.error(f"Failed to close writer: {close_err}")

    def _handle_capability(self, tag : str) -> str:
        # Advertise capabilities based on SSL context availability
        capabilities = ["IMAP4rev1", "AUTH=PLAIN", "LOGINDISABLED", "STARTTLS"]
        capability_str = " ".join(capabilities)
        return f"* CAPABILITY {capability_str}\r\n{tag} OK CAPABILITY completed\r\n"

    async def _authenticate_user(self, proxyname: str, username: str, password: str) -> bool:
        """Authenticate user with a simple placeholder mechanism"""
        return self.users.get(username) == password

    async def _handle_authenticate(self, tag: str, proxyname: str, username: str, password: str) -> str:
        if await self._authenticate_user(proxyname, username, password):
            return f"{tag} OK AUTHENTICATE completed\r\n"
        else:
            return f"{tag} NO Invalid credentials\r\n"

    async def _handle_select(self, tag: str, mailbox_name: str, user: str) -> str:
        base_mailbox_path = os.path.join(self.base_dir, user)
        try:
            # Treat INBOX as the root maildir
            if mailbox_name.upper() == 'INBOX':
                mailbox = MaildirWrapper(base_mailbox_path, create=False)
            else:
                mailbox = MaildirWrapper(base_mailbox_path, folder_name=mailbox_name, create=False)
        except FileNotFoundError:
            return f"{tag} NO [NONMAILBOX] Mailbox does not exist\r\n"

        try:
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
            response += f"* OK [PERMANENTFLAGS (\\Deleted \\Seen)] Limited\r\n"
            response += f"* OK [UIDVALIDITY {uidvalidity}] UIDs valid\r\n"
            response += f"* OK [UIDNEXT {uidnext}] Predicted next UID\r\n"
            response += f"{tag} OK [READ-WRITE] SELECT completed\r\n"
            return response

        except Exception as e:
            return f"{tag} NO [SERVERFAILURE] Server error: {str(e)}\r\n"

    async def _handle_list(self, tag: str, reference_name: str, mailbox_name: str, user: str) -> str:
        if ".." in reference_name or ".." in mailbox_name:
            return f"{tag} NO Invalid reference name\r\n"

        base_mailbox_path = os.path.join(self.base_dir, user)

        # Combine reference and mailbox name according to RFC 3501
        if mailbox_name == "":
            # Special case: return hierarchy delimiter info
            response = '* LIST (\\Noselect) "/" ""\r\n'
            return f'{response}{tag} OK LIST completed\r\n'
        elif mailbox_name.startswith("/"):
            # Absolute path: mailbox name starts with delimiter, ignore reference
            search_pattern = mailbox_name[1:]
        else:
            # Relative path: concatenate reference + mailbox
            search_pattern = reference_name + mailbox_name

        # For our flat structure, strip leading slashes and work from user's base
        search_pattern = search_pattern.lstrip("/")
        response = ""

        if search_pattern.endswith("*") or search_pattern.endswith("%"):
            # Both * and % work the same for flat structure - list all folders matching prefix
            prefix = search_pattern[:-1]
            
            try:
                # Always list INBOX first if it matches the prefix
                if "INBOX".startswith(prefix):
                    inbox_mailbox = MaildirWrapper(base_mailbox_path, folder_name="", create=False)
                    attributes = await inbox_mailbox.get_folder_attributes()
                    attr_str = " ".join(attributes)
                    response += f'* LIST ({attr_str}) "/" "INBOX"\r\n'
                
                # List all other folders that match the prefix
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
            # Looking for a specific mailbox
            
            try:
                if search_pattern == "INBOX":
                    mailbox = MaildirWrapper(base_mailbox_path, folder_name="", create=False)
                else:
                    mailbox = MaildirWrapper(base_mailbox_path, folder_name=search_pattern, create=False)
                    
                attributes = await mailbox.get_folder_attributes()
                attr_str = " ".join(attributes)
                response += f'* LIST ({attr_str}) "/" "{search_pattern}"\r\n'
                
            except FileNotFoundError:
                # Return empty response for non-existent specific mailbox (per RFC)
                pass

        return f'{response}{tag} OK LIST completed\r\n'

    async def _handle_status(self, tag: str, mailbox_name: str, item_names: str, user: str) -> str:
        """Handle STATUS <mailbox> (<items>)"""
        if mailbox_name.startswith('"') and mailbox_name.endswith('"'):
            # Remove quotes if present
            mailbox_name = mailbox_name[1:-1]  
        # Remove parentheses if present
        if item_names.startswith('(') and item_names.endswith(')'):
            item_names = item_names[1:-1]
        items = item_names.split()
        # Open the requested mailbox
        base_path = os.path.join(self.base_dir, user)
        if mailbox_name.upper() == 'INBOX':
            folder = ""
        else:
            folder = mailbox_name
        try:
            wrapper = MaildirWrapper(base_path, folder_name=folder, create=False)
        except FileNotFoundError:
            return f"{tag} NO Mailbox does not exist\r\n"
        # Collect status values
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
                # count unseen messages (no \Seen flag)
                def count_unseen():
                    total = 0
                    for k in wrapper.get_keys_safe():
                        msg = wrapper.get_message_safe(k)
                        if msg and 'S' not in msg.get_flags():
                            total += 1
                    return total
                unseen = await asyncio.to_thread(count_unseen)
                parts.append(f"UNSEEN {unseen}")
            # ignore unsupported items
        attr_str = ' '.join(parts)
        return f"* STATUS {mailbox_name} ({attr_str})\r\n{tag} OK STATUS completed\r\n"

    async def _handle_seq_fetch(self, tag: str, sequences: str, item_names: str, user: str, selected_folder: str) -> str:
        """Handle sequence-based FETCH command"""
        # Use wrapper over user's base mailbox path and selected folder
        base_path = os.path.join(self.base_dir, user)
        try:
            if selected_folder == "INBOX":
                mailbox = MaildirWrapper(base_path, folder_name="", create=False)
            else:
                mailbox = MaildirWrapper(base_path, folder_name=selected_folder, create=False)
        except FileNotFoundError:
            return f"{tag} NO [NONEXISTENT] Mailbox does not exist\r\n"
        
        message_keys = mailbox.get_keys_safe()  # Thread-safe key retrieval
        message_uid_key_pairs: List[Tuple[int, str]] = []
        
        for key in message_keys:
            uid = await mailbox.get_uid_from_key(key)
            if uid is not None:
                message_uid_key_pairs.append((uid, key))
        
        # Sort by UID to ensure consistent sequence numbering
        message_uid_key_pairs = sorted(message_uid_key_pairs, key=lambda pair: pair[0])
        
        if not message_uid_key_pairs:
            return f"{tag} OK FETCH completed\r\n"
        
        # Parse sequence set
        seq_list : List[int] = []
        try:
            # Handle comma-separated sequence sets (e.g., "1,3,5:7")
            for seq_part in sequences.split(','):
                seq_part = seq_part.strip()
                
                if ':' in seq_part:
                    start_str, end_str = seq_part.split(':')
                    start_seq = int(start_str) if start_str != '*' else len(message_uid_key_pairs)
                    
                    if end_str == '*':
                        end_seq = len(message_uid_key_pairs)
                    else:
                        end_seq = int(end_str)
                    
                    # Ensure valid range
                    start_seq = max(1, min(start_seq, len(message_uid_key_pairs)))
                    end_seq = max(1, min(end_seq, len(message_uid_key_pairs)))
                    
                    if start_seq <= end_seq:
                        seq_list.extend(range(start_seq, end_seq + 1))
                else:
                    if seq_part == '*':
                        seq_list.append(len(message_uid_key_pairs))
                    else:
                        seq_num = int(seq_part)
                        if 1 <= seq_num <= len(message_uid_key_pairs):
                            seq_list.append(seq_num)
        except ValueError:
            return f"{tag} BAD Invalid sequence set\r\n"
        
        # Remove duplicates and sort
        seq_list = sorted(set(seq_list))
        
        # Build list of (seq_num, uid, key) tuples for processing
        fetch_targets: List[Tuple[int, int, str]] = []
        for seq in seq_list:
            index = seq - 1  # Convert to 0-based index
            uid, key = message_uid_key_pairs[index]
            fetch_targets.append((seq, uid, key))
        
        return await self._handle_fetch(tag, fetch_targets, item_names, mailbox, is_uid_fetch=False)


    async def _handle_uid_fetch(self, tag: str, uids: str, item_names: str, user: str, selected_folder: str) -> str:
        """Handle UID-based FETCH command"""
        # Use wrapper over user's base mailbox path and selected folder
        base_path = os.path.join(self.base_dir, user)
        try:
            if selected_folder == "INBOX":
                mailbox = MaildirWrapper(base_path, folder_name="", create=False)
            else:
                mailbox = MaildirWrapper(base_path, folder_name=selected_folder, create=False)
        except FileNotFoundError:
            return f"{tag} NO Mailbox does not exist\r\n"
        
        # Parse UID set
        uid_list : List[int] = []
        try:
            # Handle comma-separated UID sets (e.g., "1,3,5:7")
            for uid_part in uids.split(','):
                uid_part = uid_part.strip()
                
                if ':' in uid_part:
                    start_str, end_str = uid_part.split(':')
                    if start_str == "*" and len(mailbox.get_keys_safe()) > 0:
                        start_uid = await mailbox.get_uidnext() - 1
                    else:
                        start_uid = int(start_str) if start_str != '*' else await mailbox.get_uidnext()

                    if end_str == "*":
                        end_uid = await mailbox.get_uidnext() - 1
                    else:
                        end_uid = int(end_str) if end_str != '*' else await mailbox.get_uidnext()

                    if start_uid <= end_uid:
                        uid_list.extend(range(start_uid, end_uid + 1))
                    elif start_uid > end_uid:
                        uid_list.extend(range(end_uid, start_uid + 1))
                elif uid_part == '*':
                    # Get the highest UID available
                    if len(mailbox.get_keys_safe()) > 0:
                        uid_part = await mailbox.get_uidnext() - 1
                    else:
                        uid_part = await mailbox.get_uidnext()
                    uid_list.append(uid_part)
                else:
                    uid_list.append(int(uid_part))
        except ValueError:
            return f"{tag} BAD Invalid UID set\r\n"
        
        # Remove duplicates and sort
        uid_list = sorted(set(uid_list))
        
        if not uid_list:
            return f"{tag} OK UID FETCH completed\r\n"
        
        # Build list of (seq_num, uid, key) tuples for processing
        # For UID FETCH, we need to find the sequence numbers
        message_keys = mailbox.get_keys_safe()
        message_uid_key_pairs: List[Tuple[int, str]] = []
        
        for key in message_keys:
            uid = await mailbox.get_uid_from_key(key)
            if uid is not None:
                message_uid_key_pairs.append((uid, key))
        
        # Sort by UID to ensure consistent sequence numbering
        message_uid_key_pairs = sorted(message_uid_key_pairs, key=lambda pair: pair[0])
        uid_to_seq = {uid: seq for seq, (uid, _) in enumerate(message_uid_key_pairs, 1)}

        fetch_targets: List[Tuple[int, int, str]] = []
        for uid in uid_list:
            key = await mailbox.get_key_from_uid(uid)
            if key is not None:
                seq_num = uid_to_seq.get(uid, -1)
                fetch_targets.append((seq_num, uid, key))
        
        return await self._handle_fetch(tag, fetch_targets, item_names, mailbox, is_uid_fetch=True)


    async def _handle_fetch(self, tag: str, fetch_targets: List[Tuple[int, int, str]], item_names: str, mailbox: MaildirWrapper, is_uid_fetch: bool = False) -> str:
            """Common FETCH processing for both sequence and UID FETCH"""
            # Parse fetch items
            try:
                fetcher = Fetcher()
                items = fetcher.parse_fetch_items(item_names)
            except Exception:
                return f"{tag} BAD Invalid fetch items\r\n"
            
            # Macro expansions
            MACROS = {
                'ALL': ['FLAGS', 'INTERNALDATE', 'RFC822.SIZE', 'ENVELOPE'],
                'FAST': ['FLAGS', 'INTERNALDATE', 'RFC822.SIZE'],
                'FULL': ['FLAGS', 'INTERNALDATE', 'RFC822.SIZE', 'ENVELOPE', 'BODY']
            }
            
            # Handle single macro (must be alone)
            if len(items) == 1 and items[0].upper() in MACROS:
                items = MACROS[items[0].upper()]
            
            command_name = "UID FETCH" if is_uid_fetch else "FETCH"
            
            # Accumulate responses instead of writing directly
            response = ""
            
            # Process each fetch target
            for seq_num, uid, key in fetch_targets:
                try:
                    message = mailbox.get_message_safe(key)
                    if message:
                        fetch_response = await self._handle_fetch_message(
                            seq_num, uid, key, message, items, fetcher, is_uid_fetch)
                        
                        if fetch_response:
                            response += fetch_response
                            
                except Exception as e:
                    logging.warning(f"Error processing {command_name} for seq={seq_num}, uid={uid}: {e}")
                    continue
            
            # Add command completion
            response += f"{tag} OK {command_name} completed\r\n"
            return response

    async def _handle_fetch_message(self, seq_num: int, uid: int, key: str, message: MaildirMessage, items: List[str], fetcher: Fetcher, is_uid_fetch: bool) -> str:
        """Handle FETCH for a single message"""
        # Build fetch items response
        fetch_items: List[str] = []
        
        for item in items:
            try:
                upper = item.upper()
                if upper == 'UID':
                    fetch_items.append(f'UID {uid}')
                else:
                    # Use the fetcher for other items
                    result = fetcher.handle_fetch_item(item, message)
                    if result:
                        fetch_items.append(result)
            except Exception as e:
                logging.warning(f"Error handling fetch item {item}: {e}")
                continue
        
        if not fetch_items:
            return ""
        
        # For UID FETCH, always include UID if not explicitly requested
        if is_uid_fetch and not any(item.upper() == 'UID' for item in items):
            fetch_items.insert(0, f'UID {uid}')
        
        # Format response properly, separating literals
        non_literal_parts: List[str] = []
        literal_data: str = ""


        for item in fetch_items:
            if '{' in item and '}' in item:
                # This has a literal - we need to handle it specially
                parts = item.split('\r\n', 1)
                non_literal_parts.append(parts[0])  # Just the header part with {size}
                # The actual literal data will be sent after client's continuation
                literal_data = parts[1] if len(parts) > 1 else ""
            else:
                non_literal_parts.append(item)

        if literal_data == "":
            # No literal data, just return the non-literal parts
            response = f"* {seq_num} FETCH ({' '.join(non_literal_parts)})\r\n"
            return response
        else: 
            response = f"* {seq_num} FETCH ({' '.join(non_literal_parts)}\r\n"
            response += literal_data + ')\r\n'
            return response