# Assina os executáveis com certificado de código em arquivo .pfx (secrets WINDOWS_CERT_PFX_BASE64 e
# WINDOWS_CERT_PASSWORD). Sem os secrets, não faz nada: o instalador sai sem assinatura, como antes.
param([Parameter(Mandatory = $true)][string[]]$Arquivos)
$ErrorActionPreference = "Stop"
if (-not $env:WINDOWS_CERT_PFX_BASE64) { Write-Host "Sem certificado .pfx configurado: assinatura pulada."; exit 0 }
$pfx = Join-Path $env:RUNNER_TEMP "appdv-codigo.pfx"
[IO.File]::WriteAllBytes($pfx, [Convert]::FromBase64String($env:WINDOWS_CERT_PFX_BASE64))
try {
  $signtool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin" -Recurse -Filter signtool.exe |
    Where-Object { $_.FullName -match "\\x64\\" } | Sort-Object FullName -Descending | Select-Object -First 1
  if (-not $signtool) { throw "signtool.exe não encontrado no runner." }
  foreach ($arquivo in $Arquivos) {
    & $signtool.FullName sign /f $pfx /p $env:WINDOWS_CERT_PASSWORD /fd sha256 /tr http://timestamp.digicert.com /td sha256 /d "ApPdv" $arquivo
    if ($LASTEXITCODE -ne 0) { throw "Falha ao assinar $arquivo" }
    & $signtool.FullName verify /pa $arquivo
    if ($LASTEXITCODE -ne 0) { throw "Assinatura inválida em $arquivo" }
  }
} finally {
  Remove-Item $pfx -Force -ErrorAction SilentlyContinue
}
