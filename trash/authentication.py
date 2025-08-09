# Copyright 2014-2021 The aiosmtpd Developers
# SPDX-License-Identifier: Apache-2.0

import asyncio
import logging
import secrets
import sqlite3
import sys
from functools import lru_cache
from hashlib import pbkdf2_hmac
from pathlib import Path
from smtplib import SMTP as SMTPCLient
from typing import Dict

from config import USERNAME, HOST_NAME, PASSWORD
from aiosmtpd.controller import Controller
from aiosmtpd.smtp import AuthResult, LoginPassword


DEST_PORT = 25
DB_AUTH = Path("mail.db~")


class Authenticator:
    def __init__(self):
        self.users = {USERNAME + '@' + HOST_NAME: PASSWORD}

    def __call__(self, server, session, envelope, mechanism, auth_data):
        fail_nothandled = AuthResult(success=False, handled=False)
        if mechanism not in ("LOGIN", "PLAIN"):
            return fail_nothandled
        if not isinstance(auth_data, LoginPassword):
            return fail_nothandled
        username = auth_data.login.decode()
        password = auth_data.password.decode
        if self.users.get(username) == password:
            return AuthResult(success=True)
        else:
            return AuthResult(success=False)


@lru_cache(maxsize=256)
def get_mx(domain):
    records = dns.resolver.resolve(domain, "MX")
    if not records:
        return None
    result = max(records, key=lambda r: r.preference)
    return str(result.exchange)


class RelayHandler:
    def handle_data(self, server, session, envelope, data):
        mx_rcpt: Dict[str, list[str]] = {}
        for rcpt in envelope.rcpt_tos:
            _, _, domain = rcpt.partition("@")
            mx = get_mx(domain)
            if mx is None:
                continue
            mx_rcpt.setdefault(mx, []).append(rcpt)

        for mx, rcpts in mx_rcpt.items():
            with SMTPCLient(mx, 25) as client:
                client.sendmail(
                    from_addr=envelope.mail_from,
                    to_addrs=rcpts,
                    msg=envelope.original_content
                )


# noinspection PyShadowingNames
async def amain():
    handler = RelayHandler()
    cont = Controller(
        handler,
        hostname='',
        port=8025,
        authenticator=Authenticator(DB_AUTH)
    )
    try:
        cont.start()
    finally:
        cont.stop()


if __name__ == '__main__':
    if not DB_AUTH.exists():
        print(f"Please create {DB_AUTH} first using make_user_db.py")
        sys.exit(1)
    logging.basicConfig(level=logging.DEBUG)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(amain())  # type: ignore[unused-awaitable]
    try:
        loop.run_forever()
    except KeyboardInterrupt:
        print("User abort indicated")