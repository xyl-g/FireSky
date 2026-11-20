# 临时辅助：重新生成 dist/native-windows 产物的 .sha256（LF、UTF-8 无 BOM）。
$root = "C:\Users\xy980\Desktop\FireSky\dist\native-windows"
foreach ($name in @("FireSky-1.2.0-windows-x64.zip", "FireSky-1.2.0-windows-x64-setup.exe")) {
    $p = Join-Path $root $name
    if (-not (Test-Path -LiteralPath $p)) { throw "missing: $p" }
    $hash = (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash.ToLowerInvariant()
    [System.IO.File]::WriteAllText("$p.sha256", "$hash  $name`n", [System.Text.UTF8Encoding]::new($false))
    Write-Host "$name -> $hash"
}
