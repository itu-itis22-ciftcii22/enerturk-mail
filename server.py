from aiosmtpd.controller import Controller
from file_manager import MailFile
from email.utils import parseaddr
from typing import List, cast
from email.parser import BytesParser
from email.policy import default
from email.utils import parseaddr
from aiosmtpd.smtp import SMTP, Session, Envelope


class EnerturkHandler:
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

        raw_from = cast(str, envelope.mail_from)
        _, receiver_address = parseaddr(raw_from)
        receiver = receiver_address.split("@")[0]
        mail = MailFile(receiver, "Inbox", msg)
        mail.save("mails/")

        raw_to = envelope.rcpt_tos
        for recipient in raw_to:
            _, recipient_address = parseaddr(recipient)
            recipient_name = recipient_address.split("@")[0]
            mail = MailFile(recipient_name, "Sent", msg)
            mail.save("mails/")

        return '250 Message accepted for delivery'

if __name__ == "__main__":
    controller = Controller(EnerturkHandler())
    controller.start()