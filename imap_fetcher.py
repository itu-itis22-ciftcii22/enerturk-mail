from mailbox import MaildirMessage
import time
from email.utils import formatdate, parseaddr
from typing import Any, List, Optional, Callable, Dict

class Fetcher:
    """Formatter for IMAP responses, handling various data types and structures"""
    def __init__(self):        
        # Data handlers for FETCH items (only keeping specified ones)
        self.DATA_HANDLERS : Dict[str, Callable[..., Any]] = {
            'FLAGS': self._get_flags,
            'INTERNALDATE': self._get_internal_date,
            'RFC822.SIZE': self._get_rfc822_size,
            'ENVELOPE': self._get_envelope,
        }

    def handle_fetch_item(self, item: str, msg: MaildirMessage) -> str:
        """Handle a FETCH data item and return formatted response string"""
        item_upper = item.upper()
        
        # Check direct handlers first
        if item_upper in self.DATA_HANDLERS:
            handler = self.DATA_HANDLERS[item_upper]
            data = handler(msg, item_upper)
            
            return f'{item_upper} {data}'
        
        # Return not implemented for all other items
        return f'{item_upper} (Not implemented: {item_upper})'

    # Implemented handlers
    def _get_flags(self, msg: MaildirMessage, item: str) -> str:
        """Get message flags as formatted string"""
        maildir_flags = msg.get_flags()
        flag_mapping = {
            'S': '\\Seen',
            'R': '\\Answered',
            'F': '\\Flagged',
            'T': '\\Deleted',
            'D': '\\Draft',
        }
        flags = [flag_mapping[flag] for flag in maildir_flags if flag in flag_mapping]
        return '(' + ' '.join(flags) + ')'

    def _get_internal_date(self, msg: MaildirMessage, item: str) -> str:
        """Get internal date string"""
        msg_date = msg.get_date()
        if msg_date:
            return formatdate(timeval=msg_date, localtime=False, usegmt=True)
        return formatdate(timeval=time.time(), localtime=False, usegmt=True)

    def _get_rfc822_size(self, msg: MaildirMessage, item: str) -> int:
        """Get message size in bytes"""
        return len(str(msg).encode('utf-8'))

    def _get_envelope(self, msg: MaildirMessage, item: str) -> str:
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
    
    # Helper methods for implemented handlers
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