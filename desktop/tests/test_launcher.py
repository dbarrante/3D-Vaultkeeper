import http.server
import socket
import threading


def test_find_free_port_returns_a_bindable_port():
    from launcher import find_free_port

    port = find_free_port()
    assert 1024 <= port <= 65535
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", port))  # raises OSError if somehow still held


def test_wait_for_health_returns_true_once_server_responds():
    from launcher import find_free_port, wait_for_health

    port = find_free_port()

    class OKHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass  # keep test output quiet

    server = http.server.HTTPServer(("127.0.0.1", port), OKHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert wait_for_health(f"http://127.0.0.1:{port}/", timeout_seconds=5) is True
    finally:
        server.shutdown()


def test_wait_for_health_returns_false_when_nothing_is_listening():
    from launcher import find_free_port, wait_for_health

    port = find_free_port()  # guaranteed free — nothing listens on it
    assert wait_for_health(f"http://127.0.0.1:{port}/", timeout_seconds=1) is False
