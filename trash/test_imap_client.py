#!/usr/bin/env python3

import socket
import ssl

def test_imap_connection():
    # Connect to IMAP server
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect(('localhost', 8143))
    
    def send_command(command):
        print(f">> {command}")
        sock.send((command + '\r\n').encode())
        response = sock.recv(4096).decode()
        print(f"<< {response}")
        return response
    
    # Read greeting
    greeting = sock.recv(4096).decode()
    print(f"<< {greeting}")
    
    # Test basic commands
    send_command('A01 LOGIN testuser password123')
    send_command('A02 SELECT INBOX')
    send_command('A03 UID FETCH 1:* (FLAGS)')
    send_command('A04 UID FETCH 1 (UID RFC822.SIZE FLAGS BODY.PEEK[HEADER.FIELDS (From To Subject Date)])')
    send_command('A05 LOGOUT')
    
    sock.close()

if __name__ == "__main__":
    test_imap_connection()
