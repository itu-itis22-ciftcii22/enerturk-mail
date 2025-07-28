import os
import asyncio
from smtp_server import EnerturkSMTPHandler
from imap_server import EnerturkIMAPHandler
from aiosmtpd.controller import Controller
from asyncio import start_server
from config import SMTP_PORT, IMAP_PORT, BASE_DIR
from async_storage import MaildirWrapper

PORT_FILE = "assigned_ports.txt"

async def initialize_storage():
    """Initialize the storage directory structure."""
    if not os.path.exists(BASE_DIR):
        os.makedirs(BASE_DIR)
        print(f"Created base directory: {BASE_DIR}")
    else:
        print(f"Base directory already exists: {BASE_DIR}")
    
    # Create user directory structure
    from config import USERNAME
    user_dir = os.path.join(BASE_DIR, USERNAME)
    # Ensure user directory exists and initialize it as a Maildir
    user_mailbox = MaildirWrapper(user_dir, create=True)
    print(f"Initialized Maildir at: {user_mailbox.path}")

    # Create standard mailbox subfolders as Maildir (Sent, Trash)
    for folder in ["Sent", "Trash"]:
        user_subfolder = MaildirWrapper(user_mailbox.path, folder_name=folder, create=True)
        print(f"Created mailbox folder: {user_subfolder.path}")

async def start_smtp_server():
    """Start the SMTP server."""
    smtp_handler = EnerturkSMTPHandler()
    controller = Controller(smtp_handler, hostname='localhost', port=SMTP_PORT)
    controller.start()
    assigned_port = controller.port
    print(f"SMTP server started on localhost:{assigned_port}")
    return assigned_port

async def start_imap_server():
    """Start the IMAP server."""
    imap_handler = EnerturkIMAPHandler()
    server = await start_server(imap_handler.handle_client, 'localhost', IMAP_PORT)
    assigned_port = server.sockets[0].getsockname()[1]
    print(f"IMAP server started on localhost:{assigned_port}")
    return server, assigned_port

async def main():
    await initialize_storage()
    smtp_port = await start_smtp_server()
    imap_server, imap_port = await start_imap_server()

    # Write assigned ports to a file
    with open(PORT_FILE, "w") as f:
        f.write(f"SMTP_PORT={smtp_port}\n")
        f.write(f"IMAP_PORT={imap_port}\n")

    async with imap_server:
        await imap_server.serve_forever()

if __name__ == "__main__":
    asyncio.run(main())
