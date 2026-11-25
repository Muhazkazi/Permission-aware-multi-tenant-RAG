$ErrorActionPreference = 'Stop'
Push-Location (Join-Path $PSScriptRoot '..')
$tag = 'rag-build-check:' + [Guid]::NewGuid().ToString('N')
try {
    docker build --no-cache -t $tag .
    if ($LASTEXITCODE) { throw 'Clean build failed' }
    Get-Content -Raw scripts/check_image.py | docker run --rm -i --network none --read-only --tmpfs /tmp --workdir /tmp $tag python -I -B -
    if ($LASTEXITCODE) { throw 'Isolated image check failed' }
} finally {
    docker image rm $tag
    Pop-Location
}
