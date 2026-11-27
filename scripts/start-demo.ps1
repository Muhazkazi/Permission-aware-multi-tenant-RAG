[CmdletBinding()]
param(
    [switch]$SwitchApi,
    [switch]$Stop
)

$ErrorActionPreference = 'Stop'
Push-Location (Join-Path $PSScriptRoot '..')
$demoFile = Join-Path (Get-Location) '.env.demo.local'
$compose = @('compose', '-p', 'rag-demo', '-f', 'docker-compose.yml', '--env-file', $demoFile)
$pausedId = $null
$saved = @{}

function Invoke-Demo {
    & docker @compose @args
    if ($LASTEXITCODE -ne 0) { throw "Demo Compose command failed ($($args[0])). See Docker output above." }
}

function New-Secret {
    $bytes = New-Object byte[] 32
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return [BitConverter]::ToString($bytes).Replace('-', '').ToLowerInvariant()
}

function Test-Api {
    try {
        $health = Invoke-RestMethod 'http://127.0.0.1:8000/health' -TimeoutSec 2
        return ($health.status -eq 'ok' -and $health.environment -eq 'staging')
    } catch { return $false }
}

try {
    # Compose shell variables take precedence over --env-file. Scope the
    # interpolation inputs to this invocation and restore the caller on exit.
    $base = Get-Content -Raw -LiteralPath 'docker-compose.yml'
    $names = @([regex]::Matches($base, '\$\{([A-Z_][A-Z_0-9]*)') |
        ForEach-Object { $_.Groups[1].Value }) + @('DEMO_PASSWORD_KEY', 'COMPOSE_PROFILES')
    foreach ($name in ($names | Sort-Object -Unique)) {
        $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        [Environment]::SetEnvironmentVariable($name, $null, 'Process')
    }

    if (-not (Test-Path -LiteralPath $demoFile)) {
        if ($Stop) { throw 'No managed demo configuration exists in this checkout.' }
        $volumes = docker volume ls --format '{{.Name}}'
        if ($LASTEXITCODE -ne 0) { throw 'Start Docker Desktop in Linux container mode.' }
        if ($volumes -match '^rag-demo_(mysql_data|qdrant_data|document_uploads)$') {
            throw 'Demo volumes already exist. Restore their original .env.demo.local credentials before continuing.'
        }
        $lines = @(
            'APP_ENV=staging', 'MYSQL_DATABASE=rag_demo', 'MYSQL_USER=demo_user',
            "MYSQL_PASSWORD=$(New-Secret)", "MYSQL_ROOT_PASSWORD=$(New-Secret)",
            "JWT_SECRET_KEY=$(New-Secret)", "DEMO_PASSWORD_KEY=$(New-Secret)",
            'LLM_PROVIDER=local', 'EMBEDDING_PROVIDER=local',
            'QDRANT_URL=http://qdrant:6333', 'QDRANT_COLLECTION_NAME=rag_demo_chunks',
            'CHUNK_SIZE=160', 'CHUNK_OVERLAP=24', 'API_HOST_BIND=127.0.0.1',
            'API_HOST_PORT=8000', 'MYSQL_HOST_PORT=13307', 'QDRANT_HOST_PORT=16333'
        )
        $stream = [IO.File]::Open($demoFile, 'CreateNew', 'Write', 'None')
        try {
            $bytes = [Text.Encoding]::ASCII.GetBytes(($lines -join "`n") + "`n")
            $stream.Write($bytes, 0, $bytes.Length)
        } finally { $stream.Dispose() }
    }

    $config = Invoke-Demo config --format json | Out-String | ConvertFrom-Json
    $api = $config.services.api
    $dbEnv = $config.services.mysql.environment
    $cfg = $api.environment
    if ($config.name -ne 'rag-demo' -or $dbEnv.MYSQL_DATABASE -ne 'rag_demo' -or
        $cfg.DATABASE_URL -cne "mysql+pymysql://$($dbEnv.MYSQL_USER):$($dbEnv.MYSQL_PASSWORD)@mysql:3306/rag_demo" -or
        $cfg.APP_ENV -ne 'staging' -or $cfg.QDRANT_COLLECTION_NAME -ne 'rag_demo_chunks' -or
        $cfg.QDRANT_URL -ne 'http://qdrant:6333' -or $cfg.LLM_PROVIDER -ne 'local' -or
        $cfg.EMBEDDING_PROVIDER -ne 'local' -or $api.ports[0].host_ip -ne '127.0.0.1' -or
        $api.ports[0].published -ne '8000') {
        throw 'The managed demo configuration does not match the isolated staging target.'
    }
    $mounts = @{mysql='mysql_data'; qdrant='qdrant_data'; api='document_uploads'}
    foreach ($service in $mounts.Keys) {
        $volume = $mounts[$service]
        $mount = @($config.services.$service.volumes)
        if ($mount.Count -ne 1 -or $mount[0].type -ne 'volume' -or $mount[0].source -ne $volume -or
            $config.volumes.$volume.name -ne "rag-demo_$volume") {
            throw "Unexpected $service demo volume."
        }
    }
    if ($Stop) {
        Invoke-Demo stop
        return
    }

    $keyLine = @(Get-Content -LiteralPath $demoFile | Where-Object { $_ -cmatch '^DEMO_PASSWORD_KEY=[A-Za-z0-9_-]{32,}$' })
    if ($keyLine.Count -ne 1) { throw 'Restore DEMO_PASSWORD_KEY in .env.demo.local.' }
    $env:DEMO_PASSWORD_KEY = $keyLine[0].Substring('DEMO_PASSWORD_KEY='.Length)

    $other = $null
    $ids = docker ps -q
    if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect Docker port mappings.' }
    foreach ($id in $ids) {
        $bindings = docker port $id
        if ($bindings -match ':8000$') {
            $labels = docker inspect --format '{{json .Config.Labels}}' $id | ConvertFrom-Json
            $project = $labels.'com.docker.compose.project'
            $service = $labels.'com.docker.compose.service'
            $dir = $labels.'com.docker.compose.project.working_dir'
            if ($service -ne 'api' -or $dir -ne (Get-Location).Path) {
                throw 'Port 8000 belongs to another application. Release it before starting the demo.'
            }
            if ($project -ne 'rag-demo') {
                if (-not $SwitchApi) { throw 'Port 8000 belongs to this checkout''s API. Use -SwitchApi to pause it.' }
                $other = $id
            }
        }
    }

    Invoke-Demo build api
    Invoke-Demo up -d --wait --wait-timeout 180 mysql qdrant
    # The official image initializes rag_demo and its user on first boot.
    # Also handle an existing demo instance missing only the database.
    'CREATE DATABASE IF NOT EXISTS rag_demo;' | docker @compose exec -T mysql sh -c 'MYSQL_PWD=$MYSQL_ROOT_PASSWORD exec mysql -u root'
    if ($LASTEXITCODE -ne 0) { throw 'Could not ensure the dedicated demo database exists.' }
    Invoke-Demo run --rm --no-deps api alembic upgrade head
    if ($other) {
        docker stop $other
        if ($LASTEXITCODE -ne 0) { throw 'Could not release port 8000.' }
        $pausedId = $other
    }
    Invoke-Demo up -d --no-deps api
    $ready = $false
    for ($i = 0; $i -lt 45; $i++) {
        if (Test-Api) { $ready = $true; break }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) { throw 'The staging API did not become healthy on port 8000.' }
    Invoke-Demo exec -T api python -c "import httpx; httpx.get('http://qdrant:6333/readyz', timeout=5).raise_for_status()"
    Invoke-Demo exec -T -e DEMO_PASSWORD_KEY api python -m scripts.seed_demo --staging-url http://127.0.0.1:8000 --show-logins
    Write-Host 'Swagger UI: http://127.0.0.1:8000/docs'
} catch {
    if ($pausedId) {
        docker @compose stop api
        docker start $pausedId
    }
    throw
} finally {
    foreach ($name in $saved.Keys) {
        [Environment]::SetEnvironmentVariable($name, $saved[$name], 'Process')
    }
    Pop-Location
}
