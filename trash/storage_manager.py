import os
from typing import List, Optional
from email.message import EmailMessage
from email.parser import BytesParser
from email.policy import default
from mailbox import Maildir

class MaildirWrapper:
    def __init__(self, base_dir: str, username: str, folder: str):
        self.base_dir = base_dir
        self.username = username
        self.folder = folder
        self.dir_path = os.path.join(base_dir, username, folder)
        self.maildir = Maildir(self.dir_path, create=True)

    @staticmethod
    def initialize_user_maildirs(base_dir: str, username: str, folders: Optional[List[str]] = None):
        if folders is None:
            folders = ["Inbox", "Sent"]
        for folder in folders:
            dir_path = os.path.join(base_dir, username, folder)
            Maildir(dir_path, create=True)

    def save_message(self, mail: EmailMessage):
        self.maildir.add(mail)

    def load_messages(self) -> List[EmailMessage]:
        messages = []
        for key in self.maildir.keys():
            msg = self.maildir.get_message(key)
            messages.append(msg)
        return messages

    @staticmethod
    def load_from_file(file_path: str) -> EmailMessage:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File {file_path} does not exist.")
        with open(file_path, "rb") as mail_file:
            mail = BytesParser(policy=default).parse(mail_file)
            return mail