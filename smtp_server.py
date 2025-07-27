from aiosmtpd.controller import Controller
from async_storage import MaildirWrapper
from email.utils import parseaddr
from typing import List, cast
from email.parser import BytesParser
from email.policy import default
from email.utils import parseaddr
from aiosmtpd.smtp import SMTP, Session, Envelope
from mailbox import MaildirMessage
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
        maildir_msg = MaildirMessage(msg)

        base_dir = "mails/"

        # Save to Inbox for the sender
        raw_from = cast(str, envelope.mail_from)
        _, receiver_address = parseaddr(raw_from)
        receiver = receiver_address.split("@")[0]
        inbox_wrapper = MaildirWrapper(os.path.join(base_dir, receiver, "Inbox"))
        await inbox_wrapper.save_message(maildir_msg)

        # Save to Sent for each recipient
        raw_to = envelope.rcpt_tos
        for recipient in raw_to:
            _, recipient_address = parseaddr(recipient)
            recipient_name = recipient_address.split("@")[0]
            sent_wrapper = MaildirWrapper(os.path.join(base_dir, recipient_name, "Sent"))
            await sent_wrapper.save_message(maildir_msg)

        return '250 Message accepted for delivery'

if __name__ == "__main__":
    controller = Controller(EnerturkSMTPHandler())
    controller.start()