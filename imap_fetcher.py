from mailbox import MaildirMessage
import time
import re
from email.utils import formatdate, parseaddr
from typing import Any, List, Optional, Callable, Dict

class Fetcher:
    def __init__(self):        
        # Pattern handlers for BODY expressions (processed first)
        self.PATTERN_HANDLERS = [
            (r'^BODY\[.*\]$', self._handle_body_section),
            (r'^BODY\.PEEK\[.*\]$', self._handle_body_peek_section),
            (r'^BODY\[.*\]<\d+\.\d+>$', self._handle_body_partial),
            (r'^BODY\.PEEK\[.*\]<\d+\.\d+>$', self._handle_body_peek_partial),
        ]
        
        # Data getters for FETCH items (only keeping specified ones)
        self.DATA_GETTERS : Dict[str, Callable[[MaildirMessage, str, Optional[int]], Any]] = {
            'FLAGS': self._get_flags,
            'INTERNALDATE': self._get_internal_date,
            'RFC822.SIZE': self._get_rfc822_size,
            'RFC822': self._get_rfc822,
            'ENVELOPE': self._get_envelope,
            'BODYSTRUCTURE': self._get_bodystructure,
            # Simple handlers for Thunderbird compatibility (using lambdas)
            'FROM': self._get_header_value('From', 'testuser@enerturk.com'),
            'TO': self._get_header_value('To', 'testuser@enerturk.com'),
            'CC': self._get_header_value('Cc', ''),
            'BCC': lambda msg, item, uid: 'NIL',  # BCC is usually not visible
            'SUBJECT': self._get_header_value('Subject', 'Test Email'),
            'DATE': self._get_header_value('Date', 'Mon, 1 Jan 2024 12:00:00 +0000'),
            'MESSAGE-ID': self._get_header_value('Message-ID', '<test@enerturk.com>'),
            'PRIORITY': lambda msg, item, uid: 'NIL',
            'X-PRIORITY': lambda msg, item, uid: 'NIL',
            'REFERENCES': self._get_header_value('References', ''),
            'NEWSGROUPS': lambda msg, item, uid: 'NIL',
            'IN-REPLY-TO': self._get_header_value('In-Reply-To', ''),
            'CONTENT-TYPE': self._get_header_value('Content-Type', 'text/plain'),
            'REPLY-TO': self._get_header_value('Reply-To', ''),
        }

    def parse_fetch_items(self, item_names: str) -> List[str]:
        """Parse FETCH items, handling bracketed expressions correctly"""
        if item_names.startswith('(') and item_names.endswith(')'):
            item_names = item_names[1:-1]
        
        items : List[str] = []
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

    def handle_fetch_item(self, item: str, msg: MaildirMessage, uid: Optional[int] = None) -> str:
        """Handle a FETCH data item and return formatted response string"""
        item_upper = item.upper()
        
        # Handle UID specially
        if item_upper == 'UID':
            return f'UID {uid if uid else 1}'
        
        # Try pattern handlers first (for BODY expressions)
        for pattern, handler in self.PATTERN_HANDLERS:
            if re.match(pattern, item_upper):
                return handler(msg, item, uid)
        
        # Check existing handlers in DATA_GETTERS
        if item_upper in self.DATA_GETTERS:
            handler = self.DATA_GETTERS[item_upper]
            data = handler(msg, item_upper, uid)
            return f'{item_upper} {data}'
        
        # Return stub for unknown items
        return f'{item_upper} "STUB"'

    # Implemented getters for FETCH items
    def _get_header_value(self, header_name: str, default_value: str) -> Callable[[MaildirMessage, str, Optional[int]], str]:
        """Create a typed header value getter function"""
        def handler(msg: MaildirMessage, item: str, uid: Optional[int] = None) -> str:
            return f'"{msg.get(header_name) or default_value}"'
        return handler

    def _get_flags(self, msg: MaildirMessage, item: str, uid: Optional[int] = None) -> str:
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
        # Prepend \Recent for messages still in 'new' directory (unseen and recent)
        if hasattr(msg, 'get_subdir') and msg.get_subdir() == 'new':
            flags.append('\\Recent')
        # Map persistent flags
        flags.extend(flag_mapping[flag] for flag in maildir_flags if flag in flag_mapping)
        return '(' + ' '.join(flags) + ')'

    def _get_internal_date(self, msg: MaildirMessage, item: str, uid: Optional[int] = None) -> str:
        """Get internal date as a formatted string"""
        msg_date = msg.get_date()
        if msg_date:
            return formatdate(timeval=msg_date, localtime=False, usegmt=True)
        return formatdate(timeval=time.time(), localtime=False, usegmt=True)

    def _get_rfc822_size(self, msg: MaildirMessage, item: str, uid: Optional[int] = None) -> int:
        """Get message size in bytes"""
        return len(str(msg).encode('utf-8'))

    def _get_rfc822(self, msg: MaildirMessage, item: str, uid: Optional[int] = None) -> str:
        """Get complete RFC822 message as literal string"""
        # Use as_string() for proper RFC822 format without Unix mbox header
        message_content = msg.as_string(unixfrom=False)
        # Return as a literal string with byte count
        byte_count = len(message_content.encode('utf-8'))
        return f'{{{byte_count}}}\r\n{message_content}'

    def _get_envelope(self, msg: MaildirMessage, item: str, uid: Optional[int] = None) -> str:
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
                fields.append(self._format_address_field(value))
            else:
                fields.append(self._format_string_field(value))
        
        return f'({" ".join(fields)})'
    
    def _get_bodystructure(self, msg: MaildirMessage, item: str, uid: Optional[int] = None) -> str:
        """Return a minimal BODYSTRUCTURE stub for simple text/plain messages"""
        # (type, params, subtype, id, desc, encoding, size, envelope, bodystructure, lines)
        return '("TEXT" NIL "TEXT/PLAIN" NIL NIL NIL NIL NIL)'
    
    # Helper methods for implemented getters
    def _format_address_field(self, addr_string: Optional[str]) -> str:
        """Format address field for ENVELOPE"""
        if not addr_string:
            return 'NIL'
        name, email = parseaddr(addr_string.strip())
        if '@' in email:
            mailbox, host = email.rsplit('@', 1)
        else:
            mailbox, host = email, ''
        name_part = f'"{self._escape_string(name)}"' if name else 'NIL'
        mailbox_part = f'"{self._escape_string(mailbox)}"'
        host_part = f'"{self._escape_string(host)}"'
        return f'(({name_part} NIL {mailbox_part} {host_part}))'

    def _format_string_field(self, value: Optional[str]) -> str:
        """Format string field for ENVELOPE"""
        return 'NIL' if value is None else f'"{self._escape_string(value)}"'

    def _escape_string(self, s: str) -> str:
        """Escape string for IMAP quoted string"""
        return s.replace('\\', '\\\\').replace('"', '\\"')
    
    
    # Pattern handlers for BODY expressions
    def _handle_body_section(self, msg: MaildirMessage, item: str, uid: Optional[int] = None) -> str:
        """Handle BODY[section] requests"""
        # Extract section from BODY[section]
        match = re.match(r'^BODY\[(.*)\]$', item.upper())
        if not match:
            return f'{item.upper()} "STUB"'
        
        section = match.group(1)
        
        if section == '':
            # BODY[] - full message
            content = str(msg)
            return f'BODY[] {{{len(content)}}}\r\n{content}'
        elif section.startswith('HEADER.FIELDS'):
            # BODY[HEADER.FIELDS (...)]
            return self._extract_header_fields(msg, item, section)
        elif section == 'HEADER':
            # BODY[HEADER] - just headers
            headers = self._get_message_headers(msg)
            return f'BODY[HEADER] {{{len(headers)}}}\r\n{headers}'
        elif section == 'TEXT':
            # BODY[TEXT] - just body content
            body = self._get_message_body(msg)
            return f'BODY[TEXT] {{{len(body)}}}\r\n{body}'
        else:
            return f'BODY[{section}] "STUB"'
    
    def _handle_body_peek_section(self, msg: MaildirMessage, item: str, uid: Optional[int] = None) -> str:
        """Handle BODY.PEEK[section] requests (doesn't mark as read)"""
        # Extract section from BODY.PEEK[section]
        match = re.match(r'^BODY\.PEEK\[(.*)\]$', item.upper())
        if not match:
            return f'{item.upper()} "STUB"'
        
        section = match.group(1)
        
        if section == '':
            # BODY.PEEK[] - full message
            content = str(msg)
            return f'BODY.PEEK[] {{{len(content)}}}\r\n{content}'
        elif section.startswith('HEADER.FIELDS'):
            # BODY.PEEK[HEADER.FIELDS (...)]
            return self._extract_header_fields(msg, item, section, is_peek=True)
        elif section == 'HEADER':
            # BODY.PEEK[HEADER] - just headers
            headers = self._get_message_headers(msg)
            return f'BODY.PEEK[HEADER] {{{len(headers)}}}\r\n{headers}'
        elif section == 'TEXT':
            # BODY.PEEK[TEXT] - just body content
            body = self._get_message_body(msg)
            return f'BODY.PEEK[TEXT] {{{len(body)}}}\r\n{body}'
        else:
            return f'BODY.PEEK[{section}] "STUB"'
    
    def _handle_body_partial(self, msg: MaildirMessage, item: str, uid: Optional[int] = None) -> str:
        """Handle BODY[section]<start.length> requests"""
        return f'{item.upper()} "PARTIAL_STUB"'
    
    def _handle_body_peek_partial(self, msg: MaildirMessage, item: str, uid: Optional[int] = None) -> str:
        """Handle BODY.PEEK[section]<start.length> requests"""
        return f'{item.upper()} "PARTIAL_STUB"'
    
    def _extract_header_fields(self, msg: MaildirMessage, item: str, section: str, is_peek: bool = False) -> str:
        """Extract specific header fields from message"""
        # Extract the actual headers from the message
        from_header = msg.get('From') or 'testuser@enerturk.com'
        to_header = msg.get('To') or 'testuser@enerturk.com'
        subject_header = msg.get('Subject') or 'Test Email'
        date_header = msg.get('Date') or 'Mon, 1 Jan 2024 12:00:00 +0000'
        cc_header = msg.get('Cc') or ''
        bcc_header = msg.get('Bcc') or ''
        message_id_header = msg.get('Message-ID') or '<test@enerturk.com>'
        content_type_header = msg.get('Content-Type') or 'text/plain; charset="us-ascii"'
        reply_to_header = msg.get('Reply-To') or ''
        
        # Build the header response
        headers = f"From: {from_header}\r\n"
        headers += f"To: {to_header}\r\n"
        headers += f"Subject: {subject_header}\r\n"
        headers += f"Date: {date_header}\r\n"
        if cc_header:
            headers += f"Cc: {cc_header}\r\n"
        if bcc_header:
            headers += f"Bcc: {bcc_header}\r\n"
        headers += f"Message-ID: {message_id_header}\r\n"
        headers += f"Content-Type: {content_type_header}\r\n"
        if reply_to_header:
            headers += f"Reply-To: {reply_to_header}\r\n"
        
        byte_count = len(headers.encode('utf-8'))
        
        if is_peek:
            return f'BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE CC BCC MESSAGE-ID CONTENT-TYPE REPLY-TO)] {{{byte_count}}}\r\n{headers}'
        else:
            return f'BODY[HEADER.FIELDS (FROM TO SUBJECT DATE CC BCC MESSAGE-ID CONTENT-TYPE REPLY-TO)] {{{byte_count}}}\r\n{headers}'
    
    def _get_message_headers(self, msg: MaildirMessage) -> str:
        headers = ""
        for name, value in msg.items():
            headers += f"{name}: {value}\r\n"
        return headers
    
    def _get_message_body(self, msg: MaildirMessage) -> str:
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

