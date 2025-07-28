import logging
import os
import asyncio
import shlex
# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
from typing import List, Tuple, cast
from async_storage import MaildirWrapper
from imap_fetcher import Fetcher
from config import BASE_DIR
from mailbox import MaildirMessage

class EnerturkIMAPHandler:

    def __init__(self):
        self.base_dir = BASE_DIR
        self.users = {"testuser": "password123"}  # Placeholder user credentials

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Handle individual IMAP client connection"""
        logging.info(f"IMAP connection from {writer.get_extra_info('peername')}")

        try:
            writer.write(b"* OK [CAPABILITY IMAP4rev1 LITERAL+ IDLE] Simple IMAP Server Ready\r\n")
            await writer.drain()
            
            authenticated_user = None
            selected_folder = None
            idle_tag = None
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
                        
                    logging.debug(f"IMAP << {data}")
                    
                    # Parse command
                    try:
                        lexer = shlex.shlex(data, posix=True)
                        lexer.whitespace_split = True
                        lexer.quotes = '"'
                        tokens = list(lexer)
                                
                        if tokens[0] =="DONE":
                            tag = ""
                            command = "DONE"
                            args = []
                        else:
                            tag = tokens[0]
                            command = tokens[1].upper()
                            args = tokens[2:] if len(tokens) > 2 else []
                    
                    except Exception:
                        response = "* BAD Invalid argument syntax\r\n"
                        writer.write(response.encode())
                        logging.debug(f"IMAP >> {response.strip()}")
                        await writer.drain()
                        continue
                    
                    response : str = ""

                    if command == "CAPABILITY":
                        response = self._handle_capability(tag)
                        
                    elif command == "LOGIN":
                        if authenticated_user:
                            response = f"{tag} NO [ALREADYAUTHENTICATED] Already authenticated\r\n"
                        else:
                            if len(tokens) != 4:  # tag LOGIN username password
                                response = f"{tag} BAD Invalid LOGIN command format\r\n" 
                            else:
                                username = tokens[2]
                                password = tokens[3]
                                response = self._handle_login(tag, username, password)
                                if response.startswith(f"{tag} OK"):
                                    authenticated_user = username

                    elif command == "LOGOUT":
                        response = f"* BYE IMAP4rev1 Server logging out\r\n{tag} OK LOGOUT completed\r\n"
                        writer.write(response.encode('utf-8'))
                        logging.debug(f"IMAP >> {response.strip()}")
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
                                if "OK" in response:
                                    selected_folder = args[0]
                                
                    elif command == "EXAMINE":
                        if not authenticated_user:
                            response = f"{tag} NO [AUTHENTICATIONFAILED] Not authenticated\r\n"
                        else:
                            if len(args) != 1:
                                response = f"{tag} BAD Invalid mailbox name\r\n"
                            else:
                                response = await self._handle_select(tag, args[0], cast(str, authenticated_user))
                                if "OK" in response:
                                    selected_folder = args[0]
                                
                    elif command == "LIST":
                        if not authenticated_user:
                            response = f"{tag} NO [AUTHENTICATIONFAILED] Not authenticated\r\n"
                        else:
                            if len(args) != 2:
                                response = f"{tag} BAD Invalid LIST command format\r\n"
                            else:
                                response = await self._handle_list(tag, args[0], args[1], authenticated_user, selected_folder)

                    elif command == "LSUB":
                        if not authenticated_user:
                            response = f"{tag} NO [AUTHENTICATIONFAILED] Not authenticated\r\n"
                        else:
                            if len(args) != 2:
                                response = f"{tag} BAD Invalid LSUB command format\r\n"
                            else:
                                # LSUB shows subscribed folders - for simplicity, just show same as LIST
                                response = await self._handle_list(tag, args[0], args[1], authenticated_user, selected_folder)
                                # Replace LIST with LSUB in the response
                                response = response.replace("* LIST", "* LSUB")
                                response = response.replace("LIST completed", "LSUB completed")

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
                            response = f"{tag} NO [AUTHENTICATIONFAILED] Not authenticated\r\n"
                        elif not selected_folder:
                            response = f"{tag} NO [CLIENTBUG] No folder selected\r\n"
                        elif len(args) < 2:
                            response = f"{tag} BAD Invalid UID command format\r\n"
                        elif args[0].upper() == "FETCH":
                            if len(args) < 3:
                                response = f"{tag} BAD Invalid UID FETCH command format\r\n"
                            else:
                                response = await self._handle_uid_fetch(tag, args[1], " ".join(args[2:]), authenticated_user, selected_folder)
                        elif args[0].upper() == "STORE":
                            if len(args) < 4:
                                response = f"{tag} BAD Invalid UID STORE command format\r\n"
                            else:
                                response = await self._handle_uid_store(tag, args[1:], authenticated_user, selected_folder)
                        else:
                            response = f"{tag} BAD UID subcommand '{args[0]}' not recognized\r\n"
                            
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
                            if len(args) < 2:
                                response = f"{tag} BAD Invalid STATUS command format\r\n"
                            else:
                                response = await self._handle_status(tag, args, authenticated_user)
                            
                    elif command == "SEARCH":
                        if not authenticated_user:
                            response = f"{tag} NO [AUTHENTICATIONFAILED] Not authenticated\r\n"
                        elif not selected_folder:
                            response = f"{tag} NO [NOSELECT] No folder selected\r\n"
                        else:
                            response = await self._handle_search(tag, args, authenticated_user, selected_folder)
                    
                    elif command == "STORE":
                        if not authenticated_user:
                            response = f"{tag} NO [AUTHENTICATIONFAILED] Not authenticated\r\n"
                        elif not selected_folder:
                            response = f"{tag} NO [NOSELECT] No folder selected\r\n"
                        else:
                            response = await self._handle_store(tag, args, authenticated_user, selected_folder)
                    
                    elif command == "IDLE":
                        # Minimal IDLE support
                        idle_tag = tag
                        response = f"{tag} OK IDLE completed\r\n"

                    elif command == "DONE":
                        if idle_tag:
                            response = f"{idle_tag} OK DONE completed\r\n"
                            idle_tag = None
                        else:
                            response = f"{tag} NO [IDLE] Not in IDLE mode\r\n"

                    elif command == "NOOP":
                        response = f"{tag} OK NOOP completed\r\n"
                    
                    elif command == "CREATE":
                        if not authenticated_user:
                            response = f"{tag} NO [AUTHENTICATIONFAILED] Not authenticated\r\n"
                        elif len(args) != 1:
                            response = f"{tag} BAD Invalid CREATE command format\r\n"
                        else:
                            response = self._handle_create(tag, args[0], authenticated_user)
                    # Unrecognized commands
                    else:
                        response = f"{tag} BAD Command '{command}' not recognized\r\n"
                    
                    # Send response
                    if response:
                        writer.write(response.encode('utf-8'))
                        logging.debug(f"IMAP >> {response.strip()}")
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

    def _format_fetch_response(self, fetch_items: List[str]) -> str:
        """Properly format FETCH response items, handling literals correctly"""
        if not fetch_items:
            return ""
        # Join all FETCH response items with a single space to preserve literal integrity
        return ' '.join(fetch_items)

    def _handle_capability(self, tag : str) -> str:
        # Advertise LITERAL+ for pipelined literals and IDLE for live updates
        return f"* CAPABILITY IMAP4rev1 LITERAL+ IDLE\r\n{tag} OK CAPABILITY completed\r\n"
    
    def _authenticate_user(self, username: str, password: str) -> bool:
        """Authenticate user with a simple placeholder mechanism"""
        return self.users.get(username) == password
    
    def _handle_login(self, tag: str, username: str, password: str) -> str:
        if self._authenticate_user(username, password):
            return f"{tag} OK LOGIN completed\r\n"
        else:
            return f"{tag} NO [AUTHENTICATIONFAILED] Invalid credentials\r\n"
        
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
            response += f"* OK [PERMANENTFLAGS (\\Deleted \\Seen \\*)] Limited\r\n"
            response += f"* OK [UIDVALIDITY {uidvalidity}] UIDs valid\r\n"
            response += f"* OK [UIDNEXT {uidnext}] Predicted next UID\r\n"
            response += f"{tag} OK [READ-WRITE] SELECT completed\r\n"

            return response

        except Exception as e:
            return f"{tag} NO [SERVERFAILURE] Server error: {str(e)}\r\n"

    async def _handle_list(self, tag: str, reference_name: str, mailbox_name: str, user: str, selected_folder: str | None) -> str:
        # Debug entry
        logging.debug(f"_handle_list called: tag={tag}, reference_name={reference_name}, mailbox_name={mailbox_name}, user={user}, selected_folder={selected_folder}")
        
        if ".." in reference_name or ".." in mailbox_name:
            return f"{tag} NO [NONAUTHENTICATED] Invalid reference name\r\n"

        base_mailbox_path = os.path.join(self.base_dir, user)
        logging.debug(f"_handle_list: base_path={base_mailbox_path}")

        # For flat structure, we ignore reference_name and work from the base path
        response = ""

        if mailbox_name == "":
            # Return hierarchy delimiter info
            logging.debug("_handle_list: empty mailbox_name, returning hierarchy delimiter info")
            response += '* LIST (\\Noselect) "/" ""\r\n'

        elif mailbox_name.endswith("*") or mailbox_name.endswith("%"):
            # Both * and % work the same for flat structure - list all folders matching prefix
            logging.debug(f"_handle_list: wildcard branch, prefix={mailbox_name[:-1]}")
            prefix = mailbox_name[:-1]
            
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
            logging.debug(f"_handle_list: specific mailbox branch for {mailbox_name}")
            
            try:
                if mailbox_name == "INBOX":
                    mailbox = MaildirWrapper(base_mailbox_path, folder_name="", create=False)
                else:
                    mailbox = MaildirWrapper(base_mailbox_path, folder_name=mailbox_name, create=False)
                    
                attributes = await mailbox.get_folder_attributes()
                attr_str = " ".join(attributes)
                response += f'* LIST ({attr_str}) "/" "{mailbox_name}"\r\n'
                
            except FileNotFoundError:
                # Return empty response for non-existent specific mailbox (per RFC)
                pass

        return f'{response}{tag} OK LIST completed\r\n'

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
            return f"{tag} NO [NONEXISTENT] Mailbox does not exist\r\n"
        
        # Parse UID set
        uid_list : List[int] = []
        try:
            # Handle comma-separated UID sets (e.g., "1,3,5:7")
            for uid_part in uids.split(','):
                uid_part = uid_part.strip()
                
                if ':' in uid_part:
                    start_str, end_str = uid_part.split(':')
                    start_uid = int(start_str) if start_str != '*' else await mailbox.get_uidnext() - 1
                    
                    if end_str == '*':
                        # Get the highest UID available
                        uidnext = await mailbox.get_uidnext()
                        end_uid = uidnext - 1 if uidnext > 1 else 1
                    else:
                        end_uid = int(end_str)
                    
                    if start_uid <= end_uid:
                        uid_list.extend(range(start_uid, end_uid + 1))
                else:
                    if uid_part == '*':
                        # Get the highest UID available
                        uidnext = await mailbox.get_uidnext()
                        if uidnext > 1:
                            uid_list.append(uidnext - 1)
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
                seq_num = uid_to_seq.get(uid, uid)  # Fallback to UID if sequence mapping fails
                fetch_targets.append((seq_num, uid, key))
        
        return await self._handle_fetch(tag, fetch_targets, item_names, mailbox, is_uid_fetch=True)


    async def _handle_fetch(self, tag: str, fetch_targets: List[Tuple[int, int, str]], item_names: str, mailbox: MaildirWrapper, is_uid_fetch: bool = False) -> str:
        """Common FETCH processing for both sequence and UID FETCH"""
        # Remove parentheses if present
        if item_names.startswith('(') and item_names.endswith(')'):
            item_names = item_names[1:-1]
        
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
        
        response = ""
        command_name = "UID FETCH" if is_uid_fetch else "FETCH"
        
        for seq_num, uid, key in fetch_targets:
            try:
                # Check if any of the requested items will mark the message as seen
                will_mark_seen = any(
                    item.upper() in ['RFC822', 'BODY[]'] or 
                    (item.upper().startswith('BODY[') and not item.upper().startswith('BODY.PEEK['))
                    for item in items
                )
                
                # Get message (marking as seen if needed)
                if will_mark_seen:
                    message = await mailbox.get_message_with_seen_flag(key)
                else:
                    message = mailbox.get_message_safe(key)
                    
                if message is None:
                    continue
                    
                fetch_response = await self._handle_fetch_message(seq_num, uid, key, message, items, fetcher, is_uid_fetch)
                if fetch_response:
                    response += fetch_response
                    
            except Exception as e:
                logging.warning(f"Error processing {command_name} for seq={seq_num}, uid={uid}: {e}")
                continue
        
        response += f"{tag} OK {command_name} completed\r\n"
        return response


    async def _handle_fetch_message(self, seq_num: int, uid: int, key: str, message: MaildirMessage, items: List[str], fetcher: Fetcher, is_uid_fetch: bool) -> str:
        """Handle FETCH for a single message"""
        # Build fetch items response
        fetch_items: List[str] = []
        
        for item in items:
            try:
                upper = item.upper()
                if upper == 'BODYSTRUCTURE':
                    # Minimal text/plain BODYSTRUCTURE stub
                    fetch_items.append('BODYSTRUCTURE ("TEXT" "PLAIN" ("CHARSET" "UTF-8") NIL NIL "7BIT" 0 0)')
                elif upper == 'BODY':
                    # Minimal BODY structure stub
                    fetch_items.append('BODY ("TEXT" "PLAIN" ("CHARSET" "UTF-8") NIL NIL "7BIT" 0 0)')
                elif upper == 'UID':
                    fetch_items.append(f'UID {uid}')
                else:
                    # Use the fetcher for other items
                    result = fetcher.handle_fetch_item(item, message, uid)
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
        
        # Format response properly
        formatted_items = self._format_fetch_response(fetch_items)
        if len(fetch_items) == 1:
            return f"* {seq_num} FETCH {formatted_items}\r\n"
        else:
            return f"* {seq_num} FETCH ({formatted_items})\r\n"

    async def _handle_search(self, tag: str, args: List[str], user: str, selected_folder: str) -> str:
        """Handle SEARCH command - simple implementation that returns all message sequence numbers"""
        try:
            mailbox = MaildirWrapper(os.path.join(self.base_dir, user), folder_name=selected_folder.replace("INBOX", ""), create=False)
        except Exception as e:
            logging.error(f"Error accessing mailbox for user {user}, folder {selected_folder}: {e}")
            return f"{tag} NO [NONEXISTENT] Mailbox does not exist\r\n"

        # Get all messages from both 'new' and 'cur' subdirectories
        message_keys = mailbox.get_keys_safe()  # Thread-safe key retrieval
            
        # Build list of sequence numbers (1-based)
        sequence_numbers = list(range(1, len(message_keys) + 1))
        
        if sequence_numbers:
            sequence_str = " ".join(map(str, sequence_numbers))
            response = f"* SEARCH {sequence_str}\r\n"
        else:
            response = "* SEARCH\r\n"
            
        response += f"{tag} OK SEARCH completed\r\n"
        return response

    async def _handle_store(self, tag: str, args: List[str], user: str, selected_folder: str) -> str:
        """Handle STORE command for setting message flags"""
        if len(args) < 3:
            return f"{tag} BAD STORE command requires at least 3 arguments\r\n"
        
        dirname = os.path.join(self.base_dir, user, selected_folder)
        mailbox = MaildirWrapper(dirname)
        
        # Parse arguments: STORE <sequence-set> <message-data-item-name> <value>
        sequence_set = args[0]
        data_item = args[1].upper()
        flag_list = " ".join(args[2:])
        
        # Remove parentheses from flag list if present
        if flag_list.startswith('(') and flag_list.endswith(')'):
            flag_list = flag_list[1:-1]
        
        # Parse flags - convert IMAP flags to Maildir flags
        imap_to_maildir = {
            '\\SEEN': 'S',
            '\\ANSWERED': 'R', 
            '\\FLAGGED': 'F',
            '\\DELETED': 'T',
            '\\DRAFT': 'D'
        }
        
        # Parse the flags from the command
        requested_flags: List[str] = []
        for flag in flag_list.split():
            flag = flag.strip().upper()
            if flag in imap_to_maildir:
                requested_flags.append(imap_to_maildir[flag])
        
        # Handle sequence numbers (convert to UIDs for now - simplified)
        try:
            if ':' in sequence_set:
                start_str, end_str = sequence_set.split(':')
                start_seq = int(start_str)
                end_seq = int(end_str) if end_str != '*' else 999999
                sequences = list(range(start_seq, end_seq + 1))
            else:
                sequences = [int(sequence_set)]
        except ValueError:
            return f"{tag} BAD Invalid sequence set\r\n"
        
        response = ""
        
        # Process each sequence number
        for seq in sequences:
            try:
                # For simplicity, treat sequence numbers as UIDs (this is not fully correct)
                # In a proper implementation, you'd maintain sequence-to-UID mapping
                key = await mailbox.get_key_from_uid(seq)
                if key:
                    # Handle different STORE operations
                    if data_item == 'FLAGS':
                        # Replace all flags
                        flag_string = ''.join(requested_flags)
                        if await mailbox.set_message_flags(key, flag_string):
                            # Return the new flags in response
                            message = mailbox.get_message_safe(key)
                            if message:
                                current_flags = message.get_flags()
                                flag_list_response: List[str] = []
                                for maildir_flag, imap_flag in [('S', '\\Seen'), ('R', '\\Answered'), ('F', '\\Flagged'), ('T', '\\Deleted'), ('D', '\\Draft')]:
                                    if maildir_flag in current_flags:
                                        flag_list_response.append(imap_flag)
                                response += f"* {seq} FETCH (FLAGS ({' '.join(flag_list_response)}))\r\n"
                    elif data_item == '+FLAGS':
                        # Add flags (not implemented in this simple version)
                        pass
                    elif data_item == '-FLAGS':
                        # Remove flags (not implemented in this simple version)  
                        pass
            except Exception as e:
                logging.debug(f"STORE error for sequence {seq}: {e}")
                continue
        
        response += f"{tag} OK STORE completed\r\n"
        return response

    async def _handle_uid_store(self, tag: str, args: List[str], user: str, selected_folder: str) -> str:
        """Handle UID STORE command for setting message flags by UID"""
        if len(args) < 3:
            return f"{tag} BAD UID STORE command requires at least 3 arguments\r\n"
        
        dirname = os.path.join(self.base_dir, user, selected_folder)
        mailbox = MaildirWrapper(dirname)
        
        # Parse arguments: UID STORE <uid-set> <message-data-item-name> <value>
        uid_set = args[0]
        data_item = args[1].upper()
        flag_list = " ".join(args[2:])
        
        # Remove parentheses from flag list if present
        if flag_list.startswith('(') and flag_list.endswith(')'):
            flag_list = flag_list[1:-1]
        
        # Parse flags - convert IMAP flags to Maildir flags
        imap_to_maildir = {
            '\\SEEN': 'S',
            '\\ANSWERED': 'R', 
            '\\FLAGGED': 'F',
            '\\DELETED': 'T',
            '\\DRAFT': 'D'
        }
        
        # Parse the flags from the command
        requested_flags: List[str] = []
        for flag in flag_list.split():
            flag = flag.strip().upper()
            if flag in imap_to_maildir:
                requested_flags.append(imap_to_maildir[flag])
        
        # Handle UID set
        try:
            if ':' in uid_set:
                start_str, end_str = uid_set.split(':')
                start_uid = int(start_str)
                end_uid = int(end_str) if end_str != '*' else 999999
                uids = list(range(start_uid, end_uid + 1))
            else:
                uids = [int(uid_set)]
        except ValueError:
            return f"{tag} BAD Invalid UID set\r\n"
        
        response = ""
        
        # Process each UID
        for uid in uids:
            try:
                key = await mailbox.get_key_from_uid(uid)
                if key:
                    # Handle different STORE operations
                    if data_item == 'FLAGS':
                        # Replace all flags
                        flag_string = ''.join(requested_flags)
                        if await mailbox.set_message_flags(key, flag_string):
                            # Return the new flags in response
                            message = mailbox.get_message_safe(key)
                            if message:
                                current_flags = message.get_flags()
                                flag_list_response: List[str] = []
                                for maildir_flag, imap_flag in [('S', '\\Seen'), ('R', '\\Answered'), ('F', '\\Flagged'), ('T', '\\Deleted'), ('D', '\\Draft')]:
                                    if maildir_flag in current_flags:
                                        flag_list_response.append(imap_flag)
                                response += f"* {uid} UID FETCH (FLAGS ({' '.join(flag_list_response)}))\r\n"
                    elif data_item == '+FLAGS':
                        # Add flags (not implemented in this simple version)
                        pass
                    elif data_item == '-FLAGS':
                        # Remove flags (not implemented in this simple version)  
                        pass
            except Exception as e:
                logging.debug(f"UID STORE error for UID {uid}: {e}")
                continue
        
        response += f"{tag} OK UID STORE completed\r\n"
        return response

    def _handle_create(self, tag: str, mailbox_name: str, user: str) -> str:
        """Handle CREATE command to create new mailboxes"""
        if ".." in mailbox_name:
            return f"{tag} NO [CANNOT] Invalid mailbox name\r\n"
        
        try:
            # Create the mailbox directory structure
            mailbox_path = os.path.join(self.base_dir, user, mailbox_name)
            
            if os.path.exists(mailbox_path):
                return f"{tag} NO [ALREADYEXISTS] Mailbox already exists\r\n"
            
            # Create Maildir structure
            os.makedirs(mailbox_path, exist_ok=True)
            for subdir in ["cur", "new", "tmp"]:
                subdir_path = os.path.join(mailbox_path, subdir)
                os.makedirs(subdir_path, exist_ok=True)
            
            return f"{tag} OK CREATE completed\r\n"
            
        except Exception as e:
            return f"{tag} NO [SERVERFAILURE] Server error: {str(e)}\r\n"

    async def _handle_status(self, tag: str, args: List[str], user: str) -> str:
        """Handle STATUS <mailbox> (<items>)"""
        mailbox_name = args[0]
        # Reconstruct item list (may be split across tokens)
        raw = " ".join(args[1:]).strip()
        if raw.startswith('(') and raw.endswith(')'):
            items = raw[1:-1].split()
        else:
            items = [raw]
        # Open the requested mailbox
        base_path = os.path.join(self.base_dir, user)
        try:
            wrapper = MaildirWrapper(base_path, folder_name=mailbox_name.replace("INBOX", ""), create=False)
        except FileNotFoundError:
            return f"{tag} NO [NONEXISTENT] Mailbox does not exist\r\n"
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