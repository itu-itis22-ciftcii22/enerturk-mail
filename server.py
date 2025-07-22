import asyncio
from aiosmtpd.controller import Controller
from email.parser import BytesParser
from email.policy import default
from file_manager import MailFile
from email.utils import parseaddr

class EnerturkHandler:
    def handle_AUTH(server, session, envelope, args)

    async def handle_MAIL(server, session, envelope, address, mail_options):

    async def handle_RCPT(self, server, session, envelope, address, rcpt_options):
        if not address.endswith('@enerturk.com'):
            return '550 not relaying to that domain'
        envelope.rcpt_tos.append(address)
        return '250 OK'

    async def handle_DATA(self, server, session, envelope):
        msg = BytesParser(policy=default).parsebytes(envelope.content)

        raw_from = msg.get("From", "unknown")
        _, receiver_address = parseaddr(raw_from)
        receiver = receiver_address.split("@")
        mail = MailFile(receiver, "Inbox", msg)
        mail.save("mails/")

        raw_to = msg.get("To", "unknown")
        _, sender_address = parseaddr(raw_to)
        sender = sender_address.split("@")
        mail = MailFile(sender, "Sent", msg)
        mail.save("mails/")

        return '250 Message accepted for delivery'

if __name__ == "__main__":
    controller = Controller(EnerturkHandler())
    controller.start()