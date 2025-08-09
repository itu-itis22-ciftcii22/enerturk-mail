from mailbox import MaildirMessage
import time
import re
from email.utils import formatdate, parseaddr
from email.message import Message
from email import message_from_string, message_from_bytes
from typing import List, Optional, Callable, Dict, Sequence, Tuple, Union

class Helpers:
    """Helper methods for formatting IMAP responses"""
    
    @staticmethod
    def format_address_field(addr_string: Optional[str]) -> str:
        """Format address field for ENVELOPE"""
        if not addr_string:
            return 'NIL'
        name, email = parseaddr(addr_string.strip())
        if '@' in email:
            mailbox, host = email.rsplit('@', 1)
        else:
            mailbox, host = email, ''
        name_part = f'"{name}"' if name else 'NIL'
        mailbox_part = f'"{mailbox}"'
        host_part = f'"{host}"'
        return f'(({name_part} NIL {mailbox_part} {host_part}))'

    @staticmethod
    def get_message_headers(msg: MaildirMessage) -> str:
        """Extract headers from a message"""
        headers = ""
        for name, value in msg.items():
            headers += f"{name}: {value}\r\n"
        return headers
    
    @staticmethod
    def get_message_body(msg: MaildirMessage) -> str:
        """Extract body content from a message"""
        if not msg.is_multipart():
            payload = msg.get_payload(decode=True)
            if isinstance(payload, bytes):
                charset = msg.get_content_charset('utf-8')
                return payload.decode(charset, errors='replace')
            elif isinstance(payload, str):
                return payload
            return str(payload) if payload else ""
        else:
            for part in msg.walk():
                if part.get_content_type() == 'text/plain':
                    payload = part.get_payload(decode=True)
                    if isinstance(payload, bytes):
                        charset = part.get_content_charset('utf-8')
                        return payload.decode(charset, errors='replace')
                    elif isinstance(payload, str):
                        return payload
                    return str(payload) if payload else ""
            return ""


class DataGetters:
    """Contains all getter methods for FETCH items"""
    
    @staticmethod
    def get_header_value(header_name: str, default_value: str) -> Callable[[MaildirMessage], str]:
        """Create a typed header value getter function"""
        def handler(msg: MaildirMessage) -> str:
            return f'"{msg.get(header_name) or default_value}"'
        return handler

    @staticmethod
    def get_flags(msg: MaildirMessage) -> str:
        """Get message flags as formatted string"""
        maildir_flags = msg.get_flags()
        flag_mapping = {
            'S': '\\Seen',
            'R': '\\Answered',
            'F': '\\Flagged',
            'T': '\\Deleted',
            'D': '\\Draft',
        }
        flags: List[str] = []
        # Prepend \Recent for messages still in 'new' directory
        if hasattr(msg, 'get_subdir') and msg.get_subdir() == 'new':
            flags.append('\\Recent')
        # Map persistent flags
        flags.extend(flag_mapping[flag] for flag in maildir_flags if flag in flag_mapping)
        return '(' + ' '.join(flags) + ')'

    @staticmethod
    def get_internal_date(msg: MaildirMessage) -> str:
        """Get internal date as a formatted string"""
        msg_date = msg.get_date()
        if msg_date:
            return formatdate(timeval=msg_date, localtime=False, usegmt=True)
        return formatdate(timeval=time.time(), localtime=False, usegmt=True)

    @staticmethod
    def get_rfc822_size(msg: MaildirMessage) -> str:
        """Get message size in bytes"""
        return str(len(str(msg).encode('utf-8')))

    @staticmethod
    def get_rfc822(msg: MaildirMessage) -> str:
        """Get complete RFC822 message as literal string"""
        message_content = msg.as_string(unixfrom=False)
        byte_count = len(message_content.encode('utf-8'))
        return f'{{{byte_count}}}\r\n{message_content}'

    @staticmethod
    def get_rfc822_header(msg: MaildirMessage) -> str:
        """Get message headers as literal string"""
        headers = Helpers.get_message_headers(msg)
        byte_count = len(headers.encode('utf-8'))
        return f'{{{byte_count}}}\r\n{headers}'

    @staticmethod
    def get_rfc822_text(msg: MaildirMessage) -> str:
        """Get message body as literal string"""
        body = Helpers.get_message_body(msg)
        byte_count = len(body.encode('utf-8'))
        return f'{{{byte_count}}}\r\n{body}'

    @staticmethod
    def get_envelope(msg: MaildirMessage) -> str:
        """Get ENVELOPE data as structured string"""
        envelope_data = {
            'date': msg.get('Date'),
            'subject': msg.get('Subject'),
            'from': msg.get('From'),
            'sender': msg.get('Sender'),
            'reply_to': msg.get('Reply-To'),
            'to': msg.get('To'),
            'cc': msg.get('Cc'),
            'bcc': msg.get('Bcc'),
            'in_reply_to': msg.get('In-Reply-To'),
            'message_id': msg.get('Message-ID')
        }
        
        fields: List[str] = []
        for field_name in ['date', 'subject', 'from', 'sender', 'reply_to', 'to', 'cc', 'bcc', 'in_reply_to', 'message_id']:
            value = envelope_data.get(field_name)
            if field_name in ['from', 'sender', 'reply_to', 'to', 'cc', 'bcc']:
                fields.append(Helpers.format_address_field(value))
            elif value:
                fields.append(value)
            else:
                fields.append('NIL')

        return f'({" ".join(fields)})'

    @staticmethod
    def get_bodystructure(msg: MaildirMessage, extended: bool = True) -> str:
        """Generate IMAP BODYSTRUCTURE response as defined in RFC3501."""
        # Helper functions for bodystructure
        def format_params(params: List[Tuple[str, str]]) -> str:
            """Format content parameters as IMAP string"""
            return "NIL" if not params else "(" + " ".join(f'"{n.upper()}" "{v}"' for n, v in params) + ")"

        def get_extended_fields(part: Message, extended: bool = True):
            """Get disposition, language, and location fields"""
            if not extended:
                return None, None, None
                
            # Content-Disposition with parameters
            disp_header = part.get("Content-Disposition", "")
            if disp_header:
                disp_value = part.get_content_disposition()
                if disp_value:
                    disposition = f'("{disp_value}" NIL)'
                else:
                    disposition = "NIL"
            else:
                disposition = "NIL"
                
            # Content-Language
            lang = part.get("Content-Language", "")
            if lang:
                languages = [f'"{l.strip()}"' for l in lang.split(',')]
                language = f'({" ".join(languages)})' if languages else "NIL"
            else:
                language = "NIL"
                
            # Content-Location
            loc = part.get("Content-Location", "")
            location = f'"{loc}"' if loc else "NIL"
                
            return disposition, language, location

        def format_basic_part(maintype: str, subtype: str, param_str: str, cid: str, desc: str, 
                              enc: str, size: int, lines: Optional[int] = None) -> str:
            """Format basic part structure"""
            if lines is not None:
                return f'("{maintype}" "{subtype}" {param_str} {cid} {desc} {enc} {size} {lines})'
            return f'("{maintype}" "{subtype}" {param_str} {cid} {desc} {enc} {size})'

        def convert_to_maildir_message(payload: Union[Sequence[Union[bytes, str, Message]], str, bytes, Message]) -> MaildirMessage:
            """Convert various payload types to MaildirMessage"""
            if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes)) and len(payload) > 0:
                # Get the first item from the sequence
                first_item = payload[0]
                if isinstance(first_item, str):
                    msg = message_from_string(first_item)
                elif isinstance(first_item, bytes):
                    msg = message_from_bytes(first_item)
                elif isinstance(first_item, Message):
                    msg = message_from_bytes(first_item.as_bytes())
                else:
                    # Fallback for unknown types
                    return MaildirMessage()
                return MaildirMessage(msg)
                                            
            # Convert string/bytes payload to MaildirMessage
            elif isinstance(payload, str):
                msg = message_from_string(payload)
            elif isinstance(payload, bytes):
                msg = message_from_bytes(payload)
            elif isinstance(payload, Message):
                # If it's already a Message, convert it to MaildirMessage
                return MaildirMessage(payload)
            else:
                # Fallback for other types
                return MaildirMessage()
            
            return MaildirMessage(msg)

        def fmt_part(part: MaildirMessage, extended: bool = True) -> str:
            # Basic required fields
            maintype = part.get_content_maintype().upper()
            subtype = part.get_content_subtype().upper()
            
            # Parameters (skip content-type name)
            content_params = part.get_params(("",""))
            params = content_params[1:] if isinstance(content_params, list) else []
            param_str = format_params(params)
            
            # Other required fields
            cid = f'"{part.get("Content-ID", "NIL")}"'
            desc = f'"{part.get("Content-Description", "NIL")}"'
            enc = f'"{part.get("Content-Transfer-Encoding", "7BIT").upper()}"'
            size = len(part.as_bytes())
            
            # Extended BODYSTRUCTURE fields
            disposition, language, location = get_extended_fields(part, extended)
            
            # Type-specific handling
            if maintype == "TEXT":
                lines = part.as_bytes().count(b"\n")
                basic = format_basic_part(maintype, subtype, param_str, cid, desc, enc, size, lines)
                if extended:
                    return f'{basic} {disposition} {language} {location}'
                return basic
                    
            elif maintype == "MESSAGE" and subtype == "RFC822":
                # Get the embedded message
                inner = convert_to_maildir_message(part.get_payload())

                # Required fields for message/rfc822
                env = DataGetters.get_envelope(inner)
                bs = fmt_part(inner, extended)
                
                # Count lines in the message body text
                try:
                    # Get the text content directly
                    body_text = inner.get_payload(decode=True)
                    if isinstance(body_text, bytes):
                        lines = body_text.count(b'\n')
                    else:
                        lines = str(body_text).count('\n')
                    
                    basic = f'("{maintype}" "{subtype}" {param_str} {cid} {desc} {enc} {size} {env} {bs} {lines})'
                    if extended:
                        return f'{basic} {disposition} {language} {location}'
                    return basic
                except (AttributeError, TypeError):
                    # Fallback if we can't get line count
                    pass

                # Fallback to basic format if we can't properly process as MESSAGE/RFC822
                basic = format_basic_part(maintype, subtype, param_str, cid, desc, enc, size)
                if extended:
                    return f'{basic} {disposition} {language} {location}'
                return basic
                    
            # Default case for other content types
            basic = format_basic_part(maintype, subtype, param_str, cid, desc, enc, size)
            if extended:
                return f'{basic} {disposition} {language} {location}'
            return basic

        # Handle multipart messages
        if msg.is_multipart():
            parts = [fmt_part(convert_to_maildir_message(p), extended) for p in msg.get_payload()]
            subtype = msg.get_content_subtype().upper()
            
            # Get parameters for multipart
            content_params = msg.get_params(("",""))
            params = content_params[1:] if isinstance(content_params, list) else []
            param_str = format_params(params)
            
            # Get extended fields for multipart
            disposition, language, location = get_extended_fields(msg, extended)
            
            # Extended BODYSTRUCTURE for multipart
            return f'({" ".join(parts)} "{subtype}" {param_str} {disposition} {language} {location})'
        else:
            return fmt_part(msg, extended)
        
    @staticmethod
    def get_body(msg: MaildirMessage) -> str:
        return DataGetters.get_bodystructure(msg, extended=False)

class BodyPatternHandler:
    """Handles BODY pattern expressions in FETCH commands as defined in RFC 3501"""
    
    @staticmethod
    def handle_body_section(msg: MaildirMessage, item: str) -> Optional[str]:
        """Handle BODY[section] requests"""
        match = re.match(r'^BODY\[(.*)\]$', item, re.IGNORECASE)
        if not match:
            return None
        
        section = match.group(1)
        
        if section == '':
            # BODY[] - full message
            content = str(msg)
            return f'{content}'
        elif re.match(r'^HEADER\.FIELDS\.NOT', section, re.IGNORECASE):
            # BODY[HEADER.FIELDS.NOT (...)]
            return BodyPatternHandler._extract_header_fields_not(msg, item, section)
        elif re.match(r'^HEADER\.FIELDS', section, re.IGNORECASE):
            # BODY[HEADER.FIELDS (...)]
            return BodyPatternHandler._extract_header_fields(msg, item, section)
        elif section.upper() == 'HEADER':
            # BODY[HEADER] - just headers
            headers = Helpers.get_message_headers(msg)
            return f'{headers}'
        elif section.upper() == 'TEXT':
            # BODY[TEXT] - just body content
            body = Helpers.get_message_body(msg)
            return f'{body}'
        
        # Skip unimplemented section types
        return None
    
    @staticmethod
    def handle_body_peek_section(msg: MaildirMessage, item: str) -> Optional[str]:
        """Handle BODY.PEEK[section] requests (doesn't mark as read)"""
        match = re.match(r'^BODY\.PEEK\[(.*)\]$', item, re.IGNORECASE)
        if not match:
            return None
        
        section = match.group(1)
        
        if section == '':
            # BODY.PEEK[] - full message
            content = str(msg)
            return f'{item} "{content}"'
        elif re.match(r'^HEADER\.FIELDS\.NOT', section, re.IGNORECASE):
            # BODY.PEEK[HEADER.FIELDS.NOT (...)]
            return BodyPatternHandler._extract_header_fields_not(msg, item, section, is_peek=True)
        elif re.match(r'^HEADER\.FIELDS', section, re.IGNORECASE):
            # BODY.PEEK[HEADER.FIELDS (...)]
            return BodyPatternHandler._extract_header_fields(msg, item, section, is_peek=True)
        elif section.upper() == 'HEADER':
            # BODY.PEEK[HEADER] - just headers
            headers = Helpers.get_message_headers(msg)
            return f'{item} "{headers}"'
        elif section.upper() == 'TEXT':
            # BODY.PEEK[TEXT] - just body content
            body = Helpers.get_message_body(msg)
            return f'{item} "{body}"'
        
        # Skip unimplemented section types
        return None
    
    @staticmethod
    def _extract_header_fields(msg: MaildirMessage, item: str, section: str, is_peek: bool = False) -> Optional[str]:
        """Extract specific header fields from message"""
        # Parse the header fields being requested - preserve original case
        field_match = re.match(r'HEADER\.FIELDS\s+\((.*?)\)', section, re.IGNORECASE)
        if not field_match:
            return None
        
        # Get the list of requested header field names with original case
        requested_fields = [f.strip() for f in field_match.group(1).split()]
        
        # Build the header response, including only requested fields
        headers = ""
        # Create a case-insensitive lookup dictionary that preserves original field names
        header_map = {name.lower(): (name, value) for name, value in msg.items()}
        
        for field in requested_fields:
            field_lower = field.lower()
            # Check if this field exists in message headers (case-insensitive)
            if field_lower in header_map:
                # Use the original header name from the message
                orig_name, value = header_map[field_lower]
                headers += f"{orig_name}: {value}\r\n"
        
        # Return as literal string with byte count
        byte_count = len(headers.encode('utf-8'))
        return f'{{{byte_count}}}\r\n{headers}'

    @staticmethod
    def _extract_header_fields_not(msg: MaildirMessage, item: str, section: str, is_peek: bool = False) -> Optional[str]:
        """Extract all header fields except those specified"""
        # Parse the header fields to exclude
        field_match = re.match(r'HEADER\.FIELDS\.NOT\s+\((.*?)\)', section, re.IGNORECASE)
        if not field_match:
            return None
        
        # Get the list of excluded header field names (convert to lowercase for case-insensitive comparison)
        excluded_fields = [f.strip().lower() for f in field_match.group(1).split()]
        
        # Build the header response, excluding specified fields
        headers = ""
        for name, value in msg.items():
            if name.lower() not in excluded_fields:
                headers += f"{name}: {value}\r\n"
        
        # Return as literal string with byte count
        byte_count = len(headers.encode('utf-8'))
        return f'{{{byte_count}}}\r\n{headers}'

class Fetcher:
    """Main class for handling IMAP FETCH commands"""
    
    def __init__(self):
        # Pattern handlers for BODY expressions (only include fully implemented ones)
        self.PATTERN_HANDLERS = [
            (r'^BODY\[.*\]$', BodyPatternHandler.handle_body_section),
            (r'^BODY\.PEEK\[.*\]$', BodyPatternHandler.handle_body_peek_section),
        ]
        
        # Data getters for FETCH items (only include fully implemented ones)
        self.DATA_GETTERS: Dict[str, Callable[[MaildirMessage], str]] = {
            'FLAGS': DataGetters.get_flags,
            'INTERNALDATE': DataGetters.get_internal_date,
            'RFC822.SIZE': DataGetters.get_rfc822_size,
            'RFC822': DataGetters.get_rfc822,
            'RFC822.HEADER': DataGetters.get_rfc822_header, 
            'RFC822.TEXT': DataGetters.get_rfc822_text,
            'ENVELOPE': DataGetters.get_envelope,
            'BODY': DataGetters.get_body,
            'BODYSTRUCTURE': DataGetters.get_bodystructure,
            # Header fields with proper implementations
            'FROM': DataGetters.get_header_value('From', 'testuser@enerturk.com'),
            'TO': DataGetters.get_header_value('To', 'testuser@enerturk.com'),
            'CC': DataGetters.get_header_value('Cc', ''),
            'SUBJECT': DataGetters.get_header_value('Subject', 'Test Email'),
            'DATE': DataGetters.get_header_value('Date', 'Mon, 1 Jan 2024 12:00:00 +0000'),
            'MESSAGE-ID': DataGetters.get_header_value('Message-ID', '<test@enerturk.com>'),
            'REFERENCES': DataGetters.get_header_value('References', ''),
            'IN-REPLY-TO': DataGetters.get_header_value('In-Reply-To', ''),
            'CONTENT-TYPE': DataGetters.get_header_value('Content-Type', 'text/plain'),
            'REPLY-TO': DataGetters.get_header_value('Reply-To', ''),
        }

    def parse_fetch_items(self, item_names: str) -> List[str]:
        """Parse FETCH items, handling bracketed expressions correctly"""
        if item_names.startswith('(') and item_names.endswith(')'):
            item_names = item_names[1:-1]
        
        items: List[str] = []
        current_item = ""
        bracket_depth = 0
        paren_depth = 0
        in_quotes = False
        
        for char in item_names:
            if char == '"' and not in_quotes:
                in_quotes = True
            elif char == '"' and in_quotes:
                in_quotes = False
            elif not in_quotes:
                if char == '[':
                    bracket_depth += 1
                elif char == ']':
                    bracket_depth -= 1
                elif char == '(':
                    paren_depth += 1
                elif char == ')':
                    paren_depth -= 1
                elif char == ' ' and bracket_depth == 0 and paren_depth == 0:
                    if current_item.strip():
                        items.append(current_item.strip())
                        current_item = ""
                    continue
            
            current_item += char
        
        if current_item.strip():
            items.append(current_item.strip())
        
        return items

    def handle_fetch_item(self, item: str, msg: MaildirMessage) -> Optional[str]:
        """Handle a FETCH data item and return formatted response string if implemented"""
        item_upper = item.upper()

        # Try pattern handlers first (for BODY expressions)
        for pattern, handler in self.PATTERN_HANDLERS:
            if re.match(pattern, item_upper):
                return f'{item} {handler(msg, item)}'

        # Check existing handlers in DATA_GETTERS
        if item_upper in self.DATA_GETTERS:
            handler = self.DATA_GETTERS[item_upper]
            if callable(handler):
                return f'{item} {handler(msg)}'
            else:
                return f'{item} {handler}'

        # Skip unimplemented items
        return None