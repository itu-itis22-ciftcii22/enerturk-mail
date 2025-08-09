from aiosmtpd.smtp import AuthResult, LoginPassword
from storage_manager import MaildirWrapper
from email.utils import parseaddr, formatdate, make_msgid
from typing import List, cast, Any
from email.parser import BytesParser
from email.policy import default
from email.utils import parseaddr
from aiosmtpd.smtp import SMTP, Session, Envelope
from mailbox import MaildirMessage
from authenticator import LDAPAuthenticator
import os
import logging



class Authenticator:
    def __init__(self, auth_type: str):
        self.authenticator = LDAPAuthenticator(auth_type)

    def __call__(self,
        server: SMTP, 
        session: Session, 
        envelope: Envelope, 
        mechanism: str,
        auth_data: LoginPassword | Any
    ) -> AuthResult:
        fail_nothandled = AuthResult(success=False, handled=False)
        if mechanism not in ("LOGIN", "PLAIN"):
            logging.info(f"Unsupported auth mechanism: {mechanism}")
            return fail_nothandled
        if not isinstance(auth_data, LoginPassword):
            logging.info(f"Invalid auth_data type: {type(auth_data)}")
            return fail_nothandled
        
        username = auth_data.login.decode()
        password = auth_data.password.decode()
        logging.info(f"Attempting authentication for username: {username}")
        
        if self.authenticator.authenticate_user(username, password):
            # Set session as authenticated
            session.authenticated = True
            logging.info(f"Authentication SUCCESS for {username}, session.authenticated = {session.authenticated}")
            logging.info(f"Session ID: {id(session)}")
            return AuthResult(success=True, auth_data=auth_data)
        else:
            logging.info(f"Authentication FAILED for {username}")
            return AuthResult(success=False)
                

class SMTPHandler:
    def __init__(self, mail_dir: str, host_name: str):
        self.mail_dir = mail_dir
        self.host_name = host_name

    async def handle_MAIL(
        self, 
        server: SMTP, 
        session: Session, 
        envelope: Envelope, 
        address: str, 
        mail_options: List[str]
    ) -> str:
        logging.info(f"handle_MAIL called for {address}")
        logging.info(f"Session ID: {id(session)}")
        logging.info(f"Session authenticated: {getattr(session, 'authenticated', 'NOT SET')}")
        logging.info(f"Session attributes: {dir(session)}")

        # Check if TLS is required and active
        if not getattr(session, 'ssl', False):
            logging.info(f"MAIL: TLS required")
            return '530 5.7.0 Must issue a STARTTLS command first'
        
        # Check authentication
        if not getattr(session, 'authenticated', False):
            logging.info(f"MAIL: Authentication required for {address}")
            return '530 5.7.0 Authentication required'
        
        envelope.mail_from = address
        logging.info(f"MAIL FROM: {address} accepted")
        return '250 OK'

    async def handle_RCPT(
        self, 
        server: SMTP, 
        session: Session, 
        envelope: Envelope, 
        address: str, 
        rcpt_options: List[str]
    ) -> str:
        if not getattr(session, 'authenticated', False):
            return '530 5.7.0 Authentication required'
            
        if not address.endswith('@' + self.host_name):
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
            msg['Message-ID'] = make_msgid(domain=self.host_name)
        
        # Ensure From header is present and valid
        if not msg.get('From'):
            if envelope.mail_from:
                msg['From'] = envelope.mail_from
            else:
                msg['From'] = 'unknown@' + self.host_name

        # Ensure To header is present
        if not msg.get('To') and envelope.rcpt_tos:
            msg['To'] = ', '.join(envelope.rcpt_tos)
        
        # Create MaildirMessage from the enhanced message
        maildir_msg = MaildirMessage(msg)

        # Store a copy in sender's Sent folder
        raw_from = cast(str, envelope.mail_from)
        _, sender_address = parseaddr(raw_from)
        sender_name = sender_address.split("@")[0]
        mailbox = await MaildirWrapper.create_mailbox(os.path.join(self.mail_dir, sender_name))
        sent_wrapper = MaildirWrapper(mailbox.base_path, folder_name="Sent", create=True)
        await sent_wrapper.save_message(maildir_msg)

        # Deliver message to each recipient's INBOX
        for recipient in envelope.rcpt_tos:
            _, recipient_address = parseaddr(recipient)
            recipient_name = recipient_address.split("@")[0]
            if recipient_name == sender_name:
                continue
            mailbox = await MaildirWrapper.create_mailbox(os.path.join(self.mail_dir, sender_name))
            inbox_wrapper = MaildirWrapper(mailbox.base_path, create=True)
            await inbox_wrapper.save_message(maildir_msg)

        return '250 Message accepted for delivery'