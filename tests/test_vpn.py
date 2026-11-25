# PCRLia (c) 2026 I.Arvish - Tous droits reserves - usage soumis a LICENSE.txt
# Une interface I.Arvish - edition pcrlia-partage-2026.11-1085660743c0
import http.client
import json
from pathlib import Path
import socket
import ssl
import tempfile
import threading
import unittest

from cryptography import x509
from app.server import AppServer, vpn_tls_context
from app.storage import Store
from scripts.prepare_vpn_tls import generate


class VPNConfigurationTests(unittest.TestCase):
    def test_plain_http_cannot_bind_a_network_address(self):
        with self.assertRaises(ValueError):AppServer(('192.0.2.24',0),None)

    def test_wildcard_public_loopback_linklocal_vpn_addresses_rejected(self):
        for address in ('0.0.0.0','8.8.8.8','127.0.0.1','169.254.1.2','::','::1'):
            with self.assertRaises(ValueError):vpn_tls_context(address,None,None)

    def test_private_ip_without_certificate_rejected(self):
        with self.assertRaises(ValueError):vpn_tls_context('192.0.2.24',None,None)


class VPNHTTPSTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='chlas-vpn-test-')
        cls.info=generate('127.0.0.1',Path(cls.temp.name)/'tls')
        cls.store=Store(Path(cls.temp.name)/'data')
        cls.store.setup(cls.store.setup_token,'operator','Synthetic-VPN-password-123')
        cls.store.create_user('reader','Synthetic-VPN-password-123','technician','operator')
        with cls.store.db: cls.store.db.execute("UPDATE users SET role='viewer' WHERE username='reader'")
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.minimum_version=ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(cls.info['cert_file'],cls.info['key_file'])
        cls.server=AppServer(('127.0.0.1',0),cls.store,tls_context=context)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.client_context=ssl.create_default_context(cafile=cls.info['cert_file'])
        cls.origin=f'https://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join();cls.store.db.close();cls.temp.cleanup()

    def request(self,path,data=None,headers=None):
        conn=http.client.HTTPSConnection('127.0.0.1',self.server.server_port,context=self.client_context,timeout=5)
        h={'Content-Type':'application/json','Origin':self.origin,**(headers or {})}
        conn.request('POST' if data is not None else 'GET',path,json.dumps(data) if data is not None else None,h)
        response=conn.getresponse();result=(response.status,json.loads(response.read()),dict(response.getheaders()));conn.close();return result

    def login(self,username='operator'):
        status,user,headers=self.request('/api/login',{'username':username,'password':'Synthetic-VPN-password-123'})
        self.assertEqual(status,200)
        return user,headers,{'Cookie':headers['Set-Cookie'].split(';')[0],'X-CSRF-Token':user['csrf']}

    def test_tls_verified_with_exact_certificate_and_hostname(self):
        status,body,_=self.request('/api/status');self.assertEqual(status,200);self.assertEqual(body['access'],'vpn_https');self.assertFalse(body['setup_allowed'])

    def test_secure_http_only_cookie(self):
        _,h,_=self.login();self.assertIn('; Secure',h['Set-Cookie']);self.assertIn('HttpOnly',h['Set-Cookie']);self.assertIn('SameSite=Strict',h['Set-Cookie'])

    def test_remote_bootstrap_refused(self):
        self.assertEqual(self.request('/api/setup',{})[0],403)

    def test_plaintext_origin_and_spoofed_host_refused(self):
        self.assertEqual(self.request('/api/status',headers={'Origin':self.origin.replace('https:','http:')})[0],403)
        self.assertEqual(self.request('/api/status',headers={'Host':'attacker.invalid'})[0],403)

    def test_vpn_session_can_read_and_requires_csrf(self):
        _,_,h=self.login();self.assertEqual(self.request('/api/pcr/samples',headers=h)[0],200)
        h.pop('X-CSRF-Token');self.assertEqual(self.request('/api/pcr/samples',{},h)[0],403)

    def test_vpn_viewer_cannot_write(self):
        _,_,h=self.login('reader');self.assertEqual(self.request('/api/pcr/samples',{},h)[0],403)

    def test_logout_clears_secure_cookie(self):
        _,_,h=self.login();status,_,reply=self.request('/api/logout',{},h)
        self.assertEqual(status,200);self.assertIn('; Secure',reply['Set-Cookie']);self.assertEqual(self.request('/api/pcr/samples',headers=h)[0],403)

    def test_idle_tls_client_does_not_block_other_connections(self):
        idle=socket.create_connection(('127.0.0.1',self.server.server_port),timeout=5)
        try:self.assertEqual(self.request('/api/status')[0],200)
        finally:idle.close()

    def test_certificate_never_installs_a_trust_anchor(self):
        cert=x509.load_pem_x509_certificate(Path(self.info['cert_file']).read_bytes())
        self.assertFalse(cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca)
        self.assertFalse(self.info['trust_installed']);self.assertNotIn('PRIVATE KEY',Path(self.info['public_certificate']).read_bytes().decode('latin1'))

    def test_certificate_reuse_and_ip_change_fail_closed(self):
        again=generate('127.0.0.1',Path(self.temp.name)/'tls');self.assertEqual(again['sha256'],self.info['sha256'])
        with self.assertRaises(ValueError):generate('10.1.2.99',Path(self.temp.name)/'tls')
