import unittest
from app.integrated_management import navigation_allowed


class ManagementNavigationTests(unittest.TestCase):
    def test_company_and_origin_boundary(self):
        base='https://appdv.example'
        for path in ('/empresa/2/', '/empresa/2/clientes/', '/desktop/session/', '/pedido/abc/'):
            self.assertTrue(navigation_allowed(base+path,base,2))
        for value in ('https://external.example/empresa/2/', base+'/empresa/3/', base+'/master/', 'file:///etc/passwd', 'https://user@appdv.example/empresa/2/', 'http://appdv.example/empresa/2/'):
            self.assertFalse(navigation_allowed(value,base,2))

    def test_local_preview_only_reads_selected_document(self):
        self.assertTrue(navigation_allowed('file:///tmp/cupom.html','',local_file='/tmp/cupom.html'))
        self.assertFalse(navigation_allowed('file:///tmp/secrets.txt','',local_file='/tmp/cupom.html'))
        self.assertFalse(navigation_allowed('https://appdv.example','',local_file='/tmp/cupom.html'))
