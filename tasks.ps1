param(
    [Parameter(Position = 0)]
    [ValidateSet(
        "doctor",
        "doctor-live",
        "status",
        "build",
        "verify",
        "backup",
        "server",
        "env-template",
        "report",
        "cleanup"
    )]
    [string]$Task = "doctor"
)

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"

function Invoke-Step {
    param(
        [string]$Name,
        [scriptblock]$Script
    )
    Write-Host ""
    Write-Host "==> $Name" -ForegroundColor Cyan
    $global:LASTEXITCODE = 0
    & $Script
    if ($LASTEXITCODE -ne 0) {
        throw "$Name failed with exit code $LASTEXITCODE"
    }
}

switch ($Task) {
    "doctor" {
        Invoke-Step "Doctor" { python main.py doctor }
    }
    "doctor-live" {
        Invoke-Step "Doctor live" { python main.py doctor --live }
    }
    "status" {
        Invoke-Step "Status" { python main.py status }
    }
    "build" {
        Invoke-Step "Docker build" { docker build -t fashion-affiliate-bot:local . }
    }
    "verify" {
        Invoke-Step "Compile Python" {
            python -m compileall config database core scrapers publishers captions categories drive filters music sheets utils video main.py server.py
        }
        Invoke-Step "Doctor" { python main.py doctor }
        Invoke-Step "Secret scan" {
            $SecretPatterns = @(
                "sk-[A-Za-z0-9_-]{20,}",
                "ntn_[A-Za-z0-9_-]{20,}",
                ("BEGIN PRIVATE" + " KEY"),
                ("private" + "_key"),
                "TELEGRAM_BOT_TOKEN=[0-9]+:"
            )
            $Pattern = $SecretPatterns -join "|"
            rg --hidden -g '!.git' -g '!.github/**' -g '!.env' -g '!data/**' -g '!post_preview.html' -g '!generate_preview.py' -g '!preview_post.py' $Pattern
            if ($LASTEXITCODE -eq 0) {
                throw "Secret scan found a matching value in tracked files."
            }
            if ($LASTEXITCODE -eq 1) {
                $global:LASTEXITCODE = 0
            }
        }
        Invoke-Step "Docker build" { docker build -t fashion-affiliate-bot:local . }
        Invoke-Step "Container smoke" {
            docker run --rm fashion-affiliate-bot:local python -c "from core.telegram_bot import build_application; import main, server; print('container smoke ok')"
        }
        Invoke-Step "Docker context clean" {
            docker run --rm fashion-affiliate-bot:local sh -lc "test ! -f .env && test ! -f data/fashion_bot.db && test ! -f config/service_account.json && echo docker-context-clean"
        }
    }
    "backup" {
        Invoke-Step "Backup DB" { python main.py backup-db }
    }
    "server" {
        Invoke-Step "Start server" { python server.py }
    }
    "env-template" {
        Invoke-Step "Railway env template" { python scripts/export_railway_env_template.py }
    }
    "report" {
        Invoke-Step "Write status report" { python main.py write-status-report }
    }
    "cleanup" {
        Invoke-Step "Cleanup temp" { python main.py cleanup-temp --days 14 }
    }
}
