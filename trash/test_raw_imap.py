#!/usr/bin/env python3

import socket

def test_raw_fetch():
    # Connect to IMAP server
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect(('localhost', 8143))
    
    def send_command(command):
        print(f">> {command}")
        sock.send((command + '\r\n').encode())
        response_bytes = sock.recv(8192)
        print(f"Raw bytes: {repr(response_bytes)}")
        response = response_bytes.decode()
        print(f"<< {response}")
        return response
    
    # Read greeting
    greeting_bytes = sock.recv(4096)
    print(f"Raw greeting: {repr(greeting_bytes)}")
    greeting = greeting_bytes.decode()
    print(f"<< {greeting}")
    
    # Test commands
    send_command('A01 LOGIN testuser password123')
    send_command('A02 SELECT INBOX')
    send_command('A03 UID FETCH 1 (UID FLAGS BODY.PEEK[HEADER.FIELDS (From To Subject)])')
    send_command('A04 LOGOUT')
    
    sock.close()

if __name__ == "__main__":
    test_raw_fetch()
