import unittest
from decimal import Decimal

from app import perifericos
from app.assinatura import EstadoSuspensao, codigo_erro


class FakeSerial:
    def __init__(self, resposta=b"\x0201250\x03"):
        self.resposta = resposta
        self.escrito = b""

    def __call__(self, porta, velocidade, timeout=None):
        self.porta = porta
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def reset_input_buffer(self):
        pass

    def write(self, dados):
        self.escrito += dados

    def read_until(self, fim, tamanho):
        return self.resposta


class FakeSettings(dict):
    def value(self, chave, padrao=None):
        return self.get(chave, padrao)

    def setValue(self, chave, valor):
        self[chave] = valor

    def remove(self, chave):
        self.pop(chave, None)


def ean(base12):
    soma = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(base12))
    return base12 + str((10 - soma % 10) % 10)


class PerifericosTests(unittest.TestCase):
    def test_peso_toledo(self):
        serial = FakeSerial()
        self.assertEqual(perifericos.ler_peso({"balanca_porta": "COM4"}, serial_factory=serial), Decimal("1.250"))
        self.assertEqual(serial.escrito, b"\x05")
        self.assertEqual(perifericos.interpretar_peso(b"\x02 0,385 kg\x03"), Decimal("0.385"))
        for resposta in (b"", b"\x02IIIII\x03", b"\x0200000\x03"):
            with self.assertRaises(perifericos.ErroPeriferico):
                perifericos.interpretar_peso(resposta)

    def test_gaveta_serial_e_rede(self):
        serial = FakeSerial()
        self.assertTrue(perifericos.abrir_gaveta({"gaveta_modo": "serial", "gaveta_destino": "COM3"}, serial_factory=serial))
        self.assertEqual(serial.escrito, perifericos.PULSO_GAVETA)
        enviado = []

        class Conexao:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def sendall(self, dados):
                enviado.append(dados)

        destino = []
        perifericos.abrir_gaveta({"gaveta_modo": "rede", "gaveta_destino": "10.0.0.5"},
                                 conectar=lambda endereco, timeout: destino.append(endereco) or Conexao())
        self.assertEqual((destino[0], enviado[0]), (("10.0.0.5", 9100), perifericos.PULSO_GAVETA))
        self.assertFalse(perifericos.abrir_gaveta({"gaveta_modo": ""}))

    def test_etiqueta_preco_e_peso(self):
        cfg = {"etiqueta_ativa": True, "etiqueta_prefixo": "2", "etiqueta_digitos_codigo": 4, "etiqueta_tipo": "preco"}
        codigo = ean("201230001590")
        etiqueta = perifericos.ler_etiqueta(codigo, cfg)
        self.assertEqual(etiqueta, {"codigo": "123", "tipo": "preco", "valor": Decimal("15.90")})
        produto = perifericos.produto_da_etiqueta([{"codigo": "0123", "preco_venda": "31.80"}], etiqueta)
        self.assertEqual(perifericos.quantidade_da_etiqueta(produto, etiqueta), Decimal("0.500"))
        peso = perifericos.ler_etiqueta(codigo, {**cfg, "etiqueta_tipo": "peso"})
        self.assertEqual(peso["valor"], Decimal("1.590"))
        self.assertIsNone(perifericos.ler_etiqueta(codigo[:-1] + "0" if codigo[-1] != "0" else codigo[:-1] + "1", cfg))
        self.assertIsNone(perifericos.ler_etiqueta(ean("789123456789"), cfg))
        self.assertIsNone(perifericos.ler_etiqueta(codigo, {**cfg, "etiqueta_ativa": False}))

    def test_config_salva_e_carrega(self):
        settings = FakeSettings()
        perifericos.salvar(settings, {**perifericos.PADRAO, "balanca_porta": "COM9", "etiqueta_ativa": True})
        cfg = perifericos.carregar(settings)
        self.assertEqual((cfg["balanca_porta"], cfg["etiqueta_ativa"], cfg["balanca_velocidade"]), ("COM9", True, 9600))


class Resposta:
    def __init__(self, status, dados):
        self.status_code, self.dados = status, dados

    def json(self):
        return self.dados


class Erro(Exception):
    def __init__(self, response):
        self.response = response


class AssinaturaTests(unittest.TestCase):
    def test_codigos(self):
        self.assertEqual(codigo_erro(Erro(Resposta(403, {"detail": "x", "codigo": "assinatura_suspensa"}))), "assinatura_suspensa")
        self.assertEqual(codigo_erro(Erro(Resposta(400, {"caixas": ["limite"]}))), "limite_caixas")
        self.assertEqual(codigo_erro(Erro(Resposta(403, {"detail": "Sem permissão"}))), "")
        self.assertEqual(codigo_erro(Exception("rede")), "")

    def test_tolerancia_e_trava(self):
        estado = EstadoSuspensao(FakeSettings(), tolerancia_dias=3)
        self.assertIsNone(estado.dias_restantes("7"))
        estado.registrar("7", agora=1000)
        estado.registrar("7", agora=999999)  # mantém a primeira vez
        self.assertFalse(estado.travado("7", agora=1000 + 2 * 86400))
        self.assertTrue(estado.travado("7", agora=1000 + 3 * 86400))
        estado.limpar("7")
        self.assertFalse(estado.travado("7", agora=10 ** 10))


if __name__ == "__main__":
    unittest.main()
