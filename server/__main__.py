import sys
from pathlib import Path
parent_dir = Path(__file__).parent.parent
sys.path.insert(0, str(parent_dir))
from config import HOST_NAME, SMTP_PORT, IMAP_PORT, SERVER_STORAGE_PATH, setup_logging
setup_logging()
import os
import asyncio
import ssl
import trustme
import logging
from aiosmtpd.controller import Controller
from asyncio import start_server
from smtp_server import SMTPHandler, Authenticator
from imap_server import IMAPHandler

async def initialize_storage():
    """Initialize the storage directory structure."""
    if not os.path.exists(SERVER_STORAGE_PATH):
        os.makedirs(SERVER_STORAGE_PATH)
        print(f"Created base directory: {SERVER_STORAGE_PATH}")
    else:
        print(f"Base directory already exists: {SERVER_STORAGE_PATH}")

async def start_smtp_server():
    """Start the SMTP server."""
    smtp_handler = SMTPHandler(SERVER_STORAGE_PATH, HOST_NAME)
    smtp_authenticator = Authenticator("")
    controller = Controller(smtp_handler, 
                                hostname=HOST_NAME,
                                port=SMTP_PORT, 
                                authenticator=smtp_authenticator,
                                tls_context=ssl_context
                                )
    controller.start()
    assigned_port = controller.port
    return assigned_port

async def start_imap_server():
    """Start the IMAP server."""
    imap_handler = IMAPHandler(SERVER_STORAGE_PATH, HOST_NAME, ssl_context, "")
    server = await start_server(imap_handler.handle_client, HOST_NAME, IMAP_PORT)
    assigned_port = server.sockets[0].getsockname()[1]
    return server, assigned_port

async def amain():
    await initialize_storage()
    smtp_port = await start_smtp_server()
    imap_server, imap_port = await start_imap_server()
    logging.info(f"SMTP server started on {HOST_NAME}:{smtp_port}")
    logging.info(f"IMAP server started on {HOST_NAME}:{imap_port}")

    async with imap_server:
        await imap_server.serve_forever()

if __name__ == "__main__":
    CA_KEY = "ca-key.pem"
    CA_CERT = "ca.pem"

    # load or create a persistent CA
    if not os.path.exists(CA_KEY):
        ca = trustme.CA()
        # write out the **CA** private key and the CA cert
        ca.private_key_pem.write_to_path(CA_KEY)     # ← use private_key_pem, not private_key_and_cert_chain_pem
        ca.cert_pem.write_to_path(CA_CERT)
    else:
        ca = trustme.CA.from_pem(cert_bytes=open(CA_CERT, "rb").read(), private_key_bytes=open(CA_KEY, "rb").read())

        # issue a **server** certificate
        cert = ca.issue_cert(HOST_NAME)
        # this **cert** object does have private_key_and_cert_chain_pem
        cert.private_key_and_cert_chain_pem.write_to_path("server.pem")
        ca.cert_pem.write_to_path("ca-for-client.pem")

        # create a TLS server context
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_context.load_cert_chain(certfile="server.pem")
        # For testing only - do not use in production
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE


        asyncio.run(amain())
