from aiosmtpd.controller import Controller
from async_storage import MaildirWrapper
from email.utils import parseaddr, formatdate, make_msgid
from typing import List, cast
from email.parser import BytesParser
from email.policy import default
from email.utils import parseaddr
from aiosmtpd.smtp import SMTP, Session, Envelope
from mailbox import MaildirMessage
from config import BASE_DIR
import os


class EnerturkSMTPHandler:
    # async def handle_MAIL(self, server: SMTP, session: Session, envelope: Envelope, address: str, mail_options: List[str]) -> str:

    async def handle_RCPT(
        self, 
        server: SMTP, 
        session: Session, 
        envelope: Envelope, 
        address: str, 
        rcpt_options: List[str]
    ) -> str:
        if not address.endswith('@enerturk.com'):
            return '550 not relaying to that domain'
        envelope.rcpt_tos.append(address)
        return '250 OK'

    async def handle_DATA(
        self, 
        server: SMTP, 
        session: Session, 
        envelope: Envelope
    ) -> str:
        content = cast(bytes, envelope.original_content)
        msg = BytesParser(policy=default).parsebytes(content)
        
        # Ensure required headers are present
        if not msg.get('Date'):
            msg['Date'] = formatdate(localtime=True)
        
        if not msg.get('Message-ID'):
            msg['Message-ID'] = make_msgid(domain='enerturk.com')
        
        # Ensure From header is present and valid
        if not msg.get('From'):
            if envelope.mail_from:
                msg['From'] = envelope.mail_from
            else:
                msg['From'] = 'unknown@enerturk.com'
        
        # Ensure To header is present
        if not msg.get('To') and envelope.rcpt_tos:
            msg['To'] = ', '.join(envelope.rcpt_tos)
        
        # Create MaildirMessage from the enhanced message
        maildir_msg = MaildirMessage(msg)

        # Use the configured BASE_DIR instead of hardcoded path
        base_dir = BASE_DIR

        # Store a copy in sender's Sent folder
        raw_from = cast(str, envelope.mail_from)
        _, sender_address = parseaddr(raw_from)
        sender_name = sender_address.split("@")[0]
        sent_wrapper = MaildirWrapper(os.path.join(base_dir, sender_name), folder_name="Sent", create=True)
        await sent_wrapper.save_message(maildir_msg)

        # Deliver message to each recipient's INBOX
        for recipient in envelope.rcpt_tos:
            _, recipient_address = parseaddr(recipient)
            recipient_name = recipient_address.split("@")[0]
            if recipient_name == sender_name:
                continue
            inbox_wrapper = MaildirWrapper(os.path.join(base_dir, recipient_name), create=True)
            await inbox_wrapper.save_message(maildir_msg)

        return '250 Message accepted for delivery'

if __name__ == "__main__":
    controller = Controller(EnerturkSMTPHandler())
    controller.start()