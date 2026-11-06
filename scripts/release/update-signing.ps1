param(
    [Parameter(Mandatory=$true)][ValidateSet('init', 'sign')][string]$Mode,
    [Parameter(Mandatory=$true)][string]$KeyFile,
    [string]$PayloadFile
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Security
$entropy = [Text.Encoding]::UTF8.GetBytes('E7auto update signing identity v1')
$scope = [Security.Cryptography.DataProtectionScope]::CurrentUser
if ($Mode -eq 'init') {
    if ([IO.File]::Exists($KeyFile)) { throw 'Signing identity already exists; refusing to replace it.' }
    $rsa = [Security.Cryptography.RSACng]::new(3072)
    $request = [Security.Cryptography.X509Certificates.CertificateRequest]::new(
        'CN=E7auto Update Publisher', $rsa, [Security.Cryptography.HashAlgorithmName]::SHA256,
        [Security.Cryptography.RSASignaturePadding]::Pkcs1)
    $certificate = $request.CreateSelfSigned([DateTimeOffset]::UtcNow.AddDays(-1), [DateTimeOffset]::UtcNow.AddYears(20))
    $pfx = $certificate.Export([Security.Cryptography.X509Certificates.X509ContentType]::Pfx, '')
    $protected = [Security.Cryptography.ProtectedData]::Protect($pfx, $entropy, $scope)
    $stream = [IO.File]::Open($KeyFile, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try { $stream.Write($protected, 0, $protected.Length) } finally { $stream.Dispose() }
    $payload = [Text.Encoding]::UTF8.GetBytes('E7auto update feed signature self-test v1')
} else {
    $protected = [IO.File]::ReadAllBytes($KeyFile)
    $pfx = [Security.Cryptography.ProtectedData]::Unprotect($protected, $entropy, $scope)
    $certificate = [Security.Cryptography.X509Certificates.X509Certificate2]::new(
        $pfx, '', [Security.Cryptography.X509Certificates.X509KeyStorageFlags]::EphemeralKeySet)
    $payload = [IO.File]::ReadAllBytes($PayloadFile)
}
try {
    $message = [Security.Cryptography.Pkcs.SignedCms]::new([Security.Cryptography.Pkcs.ContentInfo]::new($payload), $true)
    $signer = [Security.Cryptography.Pkcs.CmsSigner]::new($certificate)
    $signer.IncludeOption = [Security.Cryptography.X509Certificates.X509IncludeOption]::EndCertOnly
    $signer.DigestAlgorithm = [Security.Cryptography.Oid]::new('2.16.840.1.101.3.4.2.1')
    $message.ComputeSignature($signer, $true)
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $fingerprint = ([BitConverter]::ToString($sha.ComputeHash($certificate.RawData))).Replace('-', '').ToLowerInvariant() }
    finally { $sha.Dispose() }
    @{ fingerprint = $fingerprint; signature = [Convert]::ToBase64String($message.Encode()) } | ConvertTo-Json -Compress
} finally {
    [Array]::Clear($pfx, 0, $pfx.Length)
    $certificate.Dispose()
    if ($null -ne $rsa) { $rsa.Dispose() }
}
