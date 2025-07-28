import smtplib
import imaplib
import email
from email.mime.text import MIMEText
from config import SMTP_HOST, IMAP_HOST, USERNAME, PASSWORD

PORT_FILE = "assigned_ports.txt"

# Read assigned ports from the file
with open(PORT_FILE, "r") as f:
    ports = dict(line.strip().split("=") for line in f)
SMTP_PORT = int(ports["SMTP_PORT"])
IMAP_PORT = int(ports["IMAP_PORT"])

# Send an email
def send_email():
    msg = MIMEText("This is a test message.")
    msg["Subject"] = "Test Email"
    msg["From"] = USERNAME + "@enerturk.com"
    msg["To"] = USERNAME + "@enerturk.com"

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.sendmail(msg["From"], [msg["To"]], msg.as_string())
        print("Email sent successfully.")

# Fetch an email
def fetch_email():
    with imaplib.IMAP4(IMAP_HOST, IMAP_PORT) as client:
        client.login(USERNAME, PASSWORD)
        client.select("INBOX")
        status, messages = client.search(None, "ALL")

        if status != "OK":
            print("No messages found!")
            return

        for num in messages[0].split():
            status, data = client.fetch(num, "(RFC822)")
            if status != "OK" or not data or not isinstance(data[0], tuple):
                print("Failed to fetch message.")
                continue

            try:
                msg = email.message_from_bytes(data[0][1])
                print("Subject:", msg.get("Subject", "(No Subject)"))
                print("From:", msg.get("From", "(Unknown Sender)"))

                # Handle payload decoding safely
                payload = msg.get_payload(decode=True)
                if isinstance(payload, (bytes, bytearray)):
                    print("Body:", payload.decode("utf-8", errors="replace"))
                else:
                    print("Body: (No Content)")
            except Exception as e:
                print(f"Error processing message: {e}")

if __name__ == "__main__":
    send_email()
    fetch_email()
