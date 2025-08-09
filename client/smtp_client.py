import sys
from pathlib import Path
parent_dir = Path(__file__).parent.parent
sys.path.insert(0, str(parent_dir))
from config import HOST_NAME, SMTP_PORT, setup_logging
setup_logging()
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

if __name__ == "__main__":
    #username = input("Enter email username: ")
    #password = getpass.getpass("Enter email password: ")
    username = "testuser@localhost"
    password = "testpassword"
    try:
        # Connect to the SMTP server
        with smtplib.SMTP(HOST_NAME, SMTP_PORT) as server:
            server.starttls()  # Upgrade the connection to a secure encrypted SSL/TLS connection
            server.login(username, password)  # Log in to the server
            to_email = input("Enter recipient email: ")
            subject = input("Enter email subject: ")
            body = input("Enter email body: ")

            # Create the email
            msg = MIMEMultipart()
            msg['From'] = username
            msg['To'] = to_email
            msg['Subject'] = subject

            # Attach the email body
            msg.attach(MIMEText(body, 'plain'))


            server.send_message(msg)  # Send the email
            print("Email sent successfully!")
    except Exception as e:
        print(f"Failed to send email: {e}")