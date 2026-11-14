"""Wait for a local port to accept connections (used by start_all.bat)."""
import socket
import sys
import time

port = int(sys.argv[1])
deadline = time.time() + 20
while time.time() < deadline:
    with socket.socket() as sock:
        sock.settimeout(0.3)
        if sock.connect_ex(("127.0.0.1", port)) == 0:
            sys.exit(0)
    time.sleep(0.4)
sys.exit(1)
