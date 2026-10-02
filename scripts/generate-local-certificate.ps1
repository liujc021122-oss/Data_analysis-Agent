[CmdletBinding()]
param(
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$opensslCommand = Get-Command openssl -ErrorAction SilentlyContinue
if (-not $opensslCommand) {
    throw "openssl is required to generate local certificates. Install OpenSSL and try again."
}

$newCertificateCommand = Get-Command New-SelfSignedCertificate -ErrorAction SilentlyContinue
if (-not $newCertificateCommand) {
    throw "New-SelfSignedCertificate is unavailable. Run this helper on Windows with the PKI cmdlets installed."
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$certDirectory = Join-Path $repoRoot "deploy\certs"
$certificateFile = Join-Path $certDirectory "local.crt"
$keyFile = Join-Path $certDirectory "local.key"

if ((Test-Path $certificateFile -PathType Leaf -or Test-Path $keyFile -PathType Leaf) -and -not $Force) {
    throw "A local certificate already exists. Pass -Force to replace local.crt and local.key."
}

New-Item -ItemType Directory -Force -Path $certDirectory | Out-Null
$temporaryDirectory = Join-Path ([System.IO.Path]::GetTempPath()) ("data-analysis-agent-cert-" + [guid]::NewGuid())
New-Item -ItemType Directory -Force -Path $temporaryDirectory | Out-Null
$pfxFile = Join-Path $temporaryDirectory "local.pfx"
$derFile = Join-Path $temporaryDirectory "local.cer"
$temporaryCertificateFile = Join-Path $temporaryDirectory "local.crt"
$temporaryKeyFile = Join-Path $temporaryDirectory "local.key"

try {
    $certificate = New-SelfSignedCertificate `
        -DnsName "localhost" `
        -CertStoreLocation "Cert:\CurrentUser\My" `
        -KeyExportPolicy Exportable `
        -NotAfter (Get-Date).AddDays(30)
    $emptyPassword = ConvertTo-SecureString -String "" -AsPlainText -Force
    Export-PfxCertificate -Cert $certificate -FilePath $pfxFile -Password $emptyPassword | Out-Null
    Export-Certificate -Cert $certificate -FilePath $derFile -Type CERT | Out-Null

    & $opensslCommand.Source x509 -inform der -in $derFile -out $temporaryCertificateFile
    if ($LASTEXITCODE -ne 0) {
        throw "OpenSSL could not convert the generated certificate to PEM."
    }
    & $opensslCommand.Source pkcs12 -in $pfxFile -nocerts -nodes -passin pass: -out $temporaryKeyFile
    if ($LASTEXITCODE -ne 0) {
        throw "OpenSSL could not export the generated private key to PEM."
    }

    Move-Item -Path $temporaryCertificateFile -Destination $certificateFile -Force:$Force
    Move-Item -Path $temporaryKeyFile -Destination $keyFile -Force:$Force
    Write-Output "created $certificateFile and $keyFile"
}
finally {
    if ($certificate) {
        Remove-Item -LiteralPath ("Cert:\CurrentUser\My\" + $certificate.Thumbprint) -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $temporaryDirectory -Recurse -Force -ErrorAction SilentlyContinue
}
