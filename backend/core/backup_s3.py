"""Envio do backup para um armazenamento S3 compatível (AWS S3, Backblaze B2, Cloudflare R2, Wasabi…).

Assinatura AWS Signature V4 feita com a biblioteca padrão, sem dependências extras.
Endereço no estilo "path": {BACKUP_S3_ENDPOINT}/{bucket}/{chave}.
"""
import datetime
import hashlib
import hmac
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings


class ErroBackupS3(Exception):
    pass


def configurado():
    return all((settings.BACKUP_S3_ENDPOINT, settings.BACKUP_S3_BUCKET, settings.BACKUP_S3_ACCESS_KEY, settings.BACKUP_S3_SECRET_KEY))


def _assinar(chave, mensagem):
    return hmac.new(chave, mensagem.encode("utf-8"), hashlib.sha256).digest()


def requisicao_assinada(metodo, chave_objeto, conteudo=b"", agora=None):
    """Monta a requisição S3 assinada (separada para ser testada sem rede)."""
    agora = agora or datetime.datetime.now(datetime.timezone.utc)
    endpoint = urllib.parse.urlsplit(settings.BACKUP_S3_ENDPOINT.rstrip("/"))
    if endpoint.scheme != "https":
        raise ErroBackupS3("BACKUP_S3_ENDPOINT precisa começar com https://")
    caminho = "/" + urllib.parse.quote(f"{settings.BACKUP_S3_BUCKET}/{chave_objeto}", safe="/-_.~")
    data_hora = agora.strftime("%Y%m%dT%H%M%SZ")
    data = agora.strftime("%Y%m%d")
    regiao = settings.BACKUP_S3_REGION or "us-east-1"
    hash_conteudo = hashlib.sha256(conteudo).hexdigest()
    cabecalhos = {"host": endpoint.netloc, "x-amz-content-sha256": hash_conteudo, "x-amz-date": data_hora}
    nomes = ";".join(sorted(cabecalhos))
    canonica = "\n".join([metodo, caminho, "", "".join(f"{k}:{cabecalhos[k]}\n" for k in sorted(cabecalhos)), nomes, hash_conteudo])
    escopo = f"{data}/{regiao}/s3/aws4_request"
    texto = "\n".join(["AWS4-HMAC-SHA256", data_hora, escopo, hashlib.sha256(canonica.encode()).hexdigest()])
    chave = _assinar(_assinar(_assinar(_assinar(("AWS4" + settings.BACKUP_S3_SECRET_KEY).encode(), data), regiao), "s3"), "aws4_request")
    assinatura = hmac.new(chave, texto.encode(), hashlib.sha256).hexdigest()
    cabecalhos["Authorization"] = (f"AWS4-HMAC-SHA256 Credential={settings.BACKUP_S3_ACCESS_KEY}/{escopo}, "
                                   f"SignedHeaders={nomes}, Signature={assinatura}")
    url = f"{endpoint.scheme}://{endpoint.netloc}{caminho}"
    return urllib.request.Request(url, data=conteudo if metodo == "PUT" else None, method=metodo,
                                  headers={k: v for k, v in cabecalhos.items() if k != "host"})


def enviar(arquivo):
    """Envia o arquivo de backup; devolve a chave gravada no bucket."""
    if not configurado():
        raise ErroBackupS3("Backup externo não configurado (BACKUP_S3_*).")
    chave = f"{settings.BACKUP_S3_PREFIX}{arquivo.name}"
    requisicao = requisicao_assinada("PUT", chave, arquivo.read_bytes())
    try:
        with urllib.request.urlopen(requisicao, timeout=120) as resposta:
            if resposta.status >= 300:
                raise ErroBackupS3(f"S3 respondeu {resposta.status}")
    except urllib.error.HTTPError as erro:
        raise ErroBackupS3(f"S3 recusou o envio ({erro.code}): {erro.read()[:300]!r}") from erro
    except (urllib.error.URLError, TimeoutError, OSError) as erro:
        raise ErroBackupS3(f"Não foi possível falar com o S3: {erro}") from erro
    return chave
