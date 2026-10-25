param([string]$ImagePath = '', [string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$selectedImage = if ($ImagePath) { (Resolve-Path -LiteralPath $ImagePath).Path } else { '' }
Push-Location $PSScriptRoot
try {
    if (-not $selectedImage) {
        & $Python -m menu_reader.demo --out .runtime/menu-demo/images
        if ($LASTEXITCODE -ne 0) { throw 'Demo artwork generation failed' }
        $selectedImage = (Resolve-Path .runtime/menu-demo/images/printed_menu.png).Path
    }
    & $Python -m menu_reader.cli $selectedImage --out .runtime/menu-demo --interactive
    if ($LASTEXITCODE -ne 0) { throw 'Menu OCR failed; see Windows OCR/language/permissions message above' }
} finally { Pop-Location }
