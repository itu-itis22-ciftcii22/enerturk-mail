from email.message import EmailMessage
from email.parser import BytesParser
from email.policy import default
from datetime import datetime, timezone
import os
import re
import uuid

class MailFile():
    def __init__(self,  username : str, folder : str, mail : EmailMessage):
        self.username = username
        self.mail = mail
        self.mail_id = mail.get("Message-ID") or f"<{uuid.uuid4()}@enerturkmail>"
        self.folder = folder

    def save(self, base_dir : str):
        dir_path = os.path.join(base_dir, self.username, self.folder)
        os.makedirs(dir_path, exist_ok=True)
        file_path = os.path.join(dir_path, self.generate_name())
        with open(file_path, "wb") as mail_file:
            mail_file.write(self.mail.as_bytes(policy=default))

    def generate_name(self):
        sender = self.mail.get("From", "unknown").split()[0]
        sender = re.sub(r"[^\w\-]", "_", sender)[:32]
        date = self.mail.get("Date", "unknown")
        
        try:
            date_obj = datetime.strptime(date, "%a, %d %b %Y %H:%M:%S %z")
            timestamp = date_obj.strftime("%Y%m%d_%H%M%S")
        except Exception:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        
        uid = uuid.uuid4().hex[:8]
        return f"{timestamp}_{sender}_{uid}.eml"


    @classmethod
    def load_from_file(cls, file_path : str, username : str, folder : str):
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File {file_path} does not exist.")
        with open(file_path, "rb") as mail_file:
            mail = BytesParser(policy=default).parse(mail_file)
            return cls(username, folder, mail)