import os
from datetime import timedelta
import sys
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
from dotenv import load_dotenv
load_dotenv(BASE_DIR / ".env")


def env_bool(name, default):
    value = os.getenv(name)
    return default if value is None or value.strip() == "" else value.strip().lower() in ("1", "true", "yes", "on")


DEBUG = env_bool("DEBUG", False)
# `manage.py test` roda com DEBUG=False; os testes usam HTTP e cache em memória.
TESTING = len(sys.argv) > 1 and sys.argv[1] == "test"
# collectstatic roda no build da imagem, antes de as variáveis de produção existirem.
BUILDING = len(sys.argv) > 1 and sys.argv[1] == "collectstatic"

_INSECURE_KEYS = {"", "dev-only-change-me", "replace-me", "troque-por-uma-chave-aleatoria-longa"}
SECRET_KEY = os.getenv("SECRET_KEY", "").strip()
if SECRET_KEY in _INSECURE_KEYS:
    if DEBUG or TESTING or BUILDING:
        SECRET_KEY = "dev-only-insecure-key-not-for-production-use"
    else:
        # Sem chave própria, sessões e tokens JWT poderiam ser forjados.
        raise ImproperlyConfigured(
            "Defina SECRET_KEY (uma chave aleatória longa) antes de iniciar com DEBUG=False."
        )
ALLOWED_HOSTS = [h.strip() for h in os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]
CSRF_TRUSTED_ORIGINS = [origin.strip() for origin in os.getenv("CSRF_TRUSTED_ORIGINS", "").split(",") if origin.strip()]
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Produção: HTTPS obrigatório e cookies só por HTTPS. Cada item pode ser
# desligado por variável de ambiente (ex.: SECURE_SSL_REDIRECT=False) se o
# proxy à frente já fizer o redirecionamento.
_PRODUCAO = not DEBUG and not TESTING
SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", _PRODUCAO)
SESSION_COOKIE_SECURE = env_bool("SESSION_COOKIE_SECURE", _PRODUCAO)
CSRF_COOKIE_SECURE = env_bool("CSRF_COOKIE_SECURE", _PRODUCAO)
SESSION_COOKIE_HTTPONLY = True
SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "2592000" if _PRODUCAO else "0"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", False)
SECURE_HSTS_PRELOAD = False
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "rest_framework", "rest_framework_simplejwt", "core",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "core.desktop_sessions.DesktopTenantMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "core.cobranca_web.AssinaturaMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
        "core.cobranca_web.contexto",
    ]},
}]
WSGI_APPLICATION = "config.wsgi.application"
_db = os.getenv("DATABASE_URL", "")
if _db.startswith(("postgres://", "postgresql://")):
    import urllib.parse
    parsed = urllib.parse.urlparse(_db)
    query = urllib.parse.parse_qs(parsed.query)
    DATABASES = {"default": {"ENGINE": "django.db.backends.postgresql",
        "NAME": parsed.path.lstrip("/"), "USER": parsed.username, "PASSWORD": parsed.password,
        "HOST": parsed.hostname, "PORT": parsed.port or 5432,
        "OPTIONS": {"sslmode": query.get("sslmode", [os.getenv("DB_SSLMODE", "prefer")])[0]}}}
else:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}

# Os limites de tentativa precisam de um cache visto por todos os workers do
# gunicorn. Em produção usamos uma tabela no PostgreSQL (criada no entrypoint
# por `createcachetable`); nos testes e no desenvolvimento, memória local.
_cache_padrao = "db" if _db.startswith(("postgres://", "postgresql://")) and not TESTING else "locmem"
if os.getenv("CACHE_BACKEND", _cache_padrao) == "db":
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.db.DatabaseCache", "LOCATION": "appdv_cache"}}
else:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "appdv"}}
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# E-mail (redefinição de senha). Sem EMAIL_HOST, a tela "Esqueci minha senha"
# orienta a pedir uma senha provisória ao administrador.
EMAIL_HOST = os.getenv("EMAIL_HOST", "")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", EMAIL_HOST_USER or "nao-responda@localhost")
EMAIL_TIMEOUT = int(os.getenv("EMAIL_TIMEOUT", "10"))

# Endereço público do sistema (links nos e-mails). Padrão: a primeira origem de CSRF_TRUSTED_ORIGINS.
SITE_URL = os.getenv("SITE_URL", "") or (CSRF_TRUSTED_ORIGINS[0] if CSRF_TRUSTED_ORIGINS else "")
EMAIL_BACKEND = (
    "django.core.mail.backends.smtp.EmailBackend" if EMAIL_HOST
    else "django.core.mail.backends.console.EmailBackend"
)

# Cópias de segurança geradas por `manage.py backup_dados` (fora de MEDIA_ROOT: nunca públicas).
BACKUP_DIR = Path(os.getenv("BACKUP_DIR", str(BASE_DIR / "backups")))
BACKUP_MANTER = int(os.getenv("BACKUP_MANTER", "14"))
# Cópia externa dos backups (S3 compatível: AWS, Backblaze B2, Cloudflare R2, Wasabi…). Opcional.
BACKUP_S3_ENDPOINT = os.getenv("BACKUP_S3_ENDPOINT", "")
BACKUP_S3_BUCKET = os.getenv("BACKUP_S3_BUCKET", "")
BACKUP_S3_REGION = os.getenv("BACKUP_S3_REGION", "us-east-1")
BACKUP_S3_ACCESS_KEY = os.getenv("BACKUP_S3_ACCESS_KEY", "")
BACKUP_S3_SECRET_KEY = os.getenv("BACKUP_S3_SECRET_KEY", "")
BACKUP_S3_PREFIX = os.getenv("BACKUP_S3_PREFIX", "appdv/")

LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/accounts/login/"
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework_simplejwt.authentication.JWTAuthentication", "rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_THROTTLE_RATES": {
        "login_usuario": os.getenv("THROTTLE_LOGIN_USUARIO", "10/min"),
        "login_ip": os.getenv("THROTTLE_LOGIN_IP", "30/min"),
        "autorizar_desconto": os.getenv("THROTTLE_AUTORIZACAO", "10/min"),
        "aprovador": os.getenv("THROTTLE_APROVADOR", "10/min"),
    },
    # Quantos proxies confiáveis (Coolify/Traefik) ficam à frente do gunicorn.
    # Com 1, o IP do cliente vem do X-Forwarded-For definido pelo proxy.
    "NUM_PROXIES": int(os.getenv("NUM_PROXIES", "1")) if os.getenv("NUM_PROXIES", "1").strip() else None,
}
# PDV instalado: o acesso de renovação dura o mesmo prazo do login offline, para que o
# caixa que ficou sem internet volte a sincronizar sem pedir a senha de novo.
PDV_OFFLINE_DIAS = int(os.getenv("PDV_OFFLINE_DIAS", "30"))
SIMPLE_JWT = {
    "AUTH_HEADER_TYPES": ("Bearer",),
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=PDV_OFFLINE_DIAS),
}
