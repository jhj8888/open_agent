"""Real loopback TLS/HTTP, official SDK; not a Kubernetes control plane."""
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
import json
import socket
import ssl
import threading

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from kubernetes import client
import pytest
from urllib3.util.retry import Retry

from agent.durable.cluster_control import ClusterSnapshot
from agent.durable.cluster_process import ClusterProcessError, ClusterProgram, KubernetesProcesses
from agent.tests.test_cluster_control import snapshot


@pytest.fixture
def endpoint(tmp_path):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'loopback-contract-test')])
    now = datetime.now(timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(minutes=30))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ip_address('127.0.0.1'))]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True).sign(key, hashes.SHA256()))
    cert_path, key_path = tmp_path / 'tls.crt', tmp_path / 'tls.key'
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    row = ClusterSnapshot.model_validate(snapshot()).model_copy(update={'phase': 'launching'})
    state = {'creates': 0, 'redirect': False, 'requests': 0, 'created': None}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass

        def do_GET(self):
            state['requests'] += 1
            if state['redirect']:
                self.send_response(307)
                self.send_header('Location', '/redirect-target')
                self.end_headers()
                return
            if self.path.endswith('/kube-system'):
                value = {'metadata': {'name': 'kube-system', 'uid': row.target.clusterId}, 'status': {'phase': 'Active'}}
            elif self.path.endswith('/' + row.target.namespace):
                value = {'metadata': {'name': row.target.namespace, 'uid': row.target.namespaceUid}, 'status': {'phase': 'Active'}}
            else:
                value = {'kind': 'Status', 'reason': 'NotFound', 'code': 404}
            self.send_response(404 if value.get('code') == 404 else 200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(value).encode())

        def do_POST(self):
            state['creates'] += 1
            state['created'] = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            # The write reached the API. Lose the reply after accepting it.
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(cert_path), str(key_path))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    configuration = client.Configuration()
    configuration.host = f'https://127.0.0.1:{server.server_port}'
    configuration.ssl_ca_cert = str(cert_path)
    configuration.retries = Retry(total=0, connect=0, read=0, redirect=0, status=0)
    api = client.ApiClient(configuration)
    driver = KubernetesProcesses(api, row.target, ClusterProgram('worker', 'executor', 'trusted'))
    try:
        yield row, driver, state
    finally:
        api.close()
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.mark.asyncio
async def test_actual_https_create_with_lost_response_has_no_transport_retry(endpoint):
    row, driver, state = endpoint
    with pytest.raises(ClusterProcessError, match='^CLUSTER_OPERATION_UNCONFIRMED$'):
        await driver.launch(row)
    assert state['creates'] == 1
    assert state['created']['metadata']['name'] == row.podName
    assert state['requests'] == 3  # Both namespace identities and missing Pod.


@pytest.mark.asyncio
async def test_actual_https_does_not_follow_redirects(endpoint):
    row, driver, state = endpoint
    state['redirect'] = True
    with pytest.raises(ClusterProcessError, match='^CLUSTER_OPERATION_UNCONFIRMED$'):
        await driver.launch(row)
    assert state['requests'] == 1 and state['creates'] == 0
