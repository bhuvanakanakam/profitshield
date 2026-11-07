"""Boot the real CLI entry point with disposable storage and no provider keys."""
import http.cookiejar
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

from owner_auth import OwnerAuth

ROOT=Path(__file__).resolve().parents[1]


class RuntimeSmokeTests(unittest.TestCase):
    def test_real_startup_login_connected_backend_and_logout(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary)
            with socket.socket() as probe:
                probe.bind(('127.0.0.1',0))
                port=probe.getsockname()[1]
            origin='http://127.0.0.1:'+str(port)
            auth_path=directory/'owner.sqlite3'
            auth=OwnerAuth(auth_path,origin,local_http=True)
            password='isolated-runtime-test-password'
            auth.set_password(password)
            environment={**os.environ,'PORT':str(port),'PROFITSHIELD_ORIGIN':origin,
                         'PROFITSHIELD_AUTH_DB':str(auth_path),'BACKHOUSE_DB':str(directory/'restaurant.db'),
                         'OPENROUTER_API_KEY':'','SQUARE_ACCESS_TOKEN':'','PROFITSHIELD_AI_PRIVACY_APPROVED':'',
                         'PYTHONDONTWRITEBYTECODE':'1'}
            # Exercise automatic loopback-cookie mode, as used by plain server.py.
            environment.pop('PROFITSHIELD_LOCAL_HTTP',None)
            jar=http.cookiejar.CookieJar()
            client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
            def request(path,payload=None,csrf=None):
                headers={'Origin':origin}
                body=None
                if payload is not None:
                    headers['Content-Type']='application/json'
                    body=json.dumps(payload).encode()
                if csrf:headers['X-CSRF-Token']=csrf
                try:
                    with client.open(urllib.request.Request(origin+path,data=body,headers=headers),timeout=3) as response:
                        return response.status,response.read()
                except urllib.error.HTTPError as error:
                    return error.code,error.read()
            with (directory/'server.log').open('w') as output:
                process=subprocess.Popen([sys.executable,str(ROOT/'server.py')],cwd=ROOT,env=environment,stdout=output,stderr=output)
                try:
                    for _ in range(100):
                        try:
                            status,page=request('/')
                            break
                        except OSError:
                            if process.poll() is not None:
                                self.fail('Server exited before serving requests')
                            time.sleep(.05)
                    else:self.fail('Server startup timed out')
                    self.assertEqual(status,200)
                    self.assertIn(b'Owner password',page)
                    self.assertEqual(request('/api/data')[0],401)
                    self.assertEqual(request('/api/prototype')[0],401)
                    status,body=request('/api/auth/login',{'password':password})
                    self.assertEqual(status,200)
                    csrf=json.loads(body)['csrf']
                    self.assertEqual(request('/api/prototype/load-golden',{},csrf)[0],200)
                    status,body=request('/api/prototype')
                    self.assertEqual(status,200)
                    self.assertEqual(json.loads(body)['baseline']['money_cents']['contribution_margin'],470000)
                    self.assertEqual(request('/api/auth/logout',{},csrf)[0],200)
                    self.assertEqual(request('/api/data')[0],401)
                finally:
                    process.terminate()
                    try:process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill();process.wait(timeout=5)
