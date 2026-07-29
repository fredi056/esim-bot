$ErrorActionPreference = "Stop"

$BotRepo = Resolve-Path (Join-Path $PSScriptRoot "..")
$MiniAppRepo = "C:\Users\1\Documents\Codex\2026-07-09\telegram-python-1-user-id-2\esim-miniapp"
$SourceFile = Join-Path $BotRepo "country_prices.json"
$TargetFile = Join-Path $MiniAppRepo "src\data\country_prices.json"
$TargetGitPath = "src/data/country_prices.json"
$ForbiddenCountries = @("Russia", "Russian Federation", "Guadeloupe")

function Write-Step {
    param([string]$Message)
    Write-Host "==> $Message"
}

function Get-GitExe {
    $git = Get-Command git -ErrorAction SilentlyContinue
    if ($git) {
        return $git.Source
    }

    $bundledGit = "C:\Users\1\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe"
    if (Test-Path -LiteralPath $bundledGit) {
        $env:GIT_EXEC_PATH = "C:\Users\1\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\mingw64\bin"
        $env:Path = "C:\Users\1\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\mingw64\bin;C:\Users\1\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd;$env:Path"
        return $bundledGit
    }

    throw "Git не найден. Установите Git или добавьте git.exe в PATH."
}

function Invoke-Git {
    param(
        [string]$Repo,
        [string[]]$GitArgs
    )

    $output = & $script:GitExe -C $Repo @GitArgs 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Git error in ${Repo}: git $($GitArgs -join ' ')`n$output"
    }
    return $output
}

function Assert-Repo {
    param([string]$Repo)

    if (-not (Test-Path -LiteralPath $Repo -PathType Container)) {
        throw "Репозиторий не найден: $Repo"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $Repo ".git"))) {
        throw "Папка не является Git-репозиторием: $Repo"
    }
}

function Assert-CleanTree {
    param([string]$Repo)

    $status = Invoke-Git -Repo $Repo -GitArgs @("status", "--short")
    if ($status) {
        throw "Рабочее дерево не чистое: $Repo`n$status"
    }
}

function Assert-ValidPriceJson {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Файл цен не найден: $Path"
    }

    $json = Get-Content -Raw -Encoding UTF8 -LiteralPath $Path | ConvertFrom-Json
    $countries = $json.PSObject.Properties.Name
    if (-not $countries -or $countries.Count -eq 0) {
        throw "JSON цен должен быть непустым объектом."
    }

    foreach ($country in $ForbiddenCountries) {
        if ($countries -contains $country) {
            throw "Запрещённая страна найдена в JSON: $country"
        }
    }
}

function Get-Sha256 {
    param([string]$Path)
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash
}

$script:GitExe = Get-GitExe
$backup = $null
$copied = $false

try {
    Write-Step "Проверяю репозитории и файлы"
    Assert-Repo $BotRepo
    Assert-Repo $MiniAppRepo
    Assert-CleanTree $BotRepo
    Assert-CleanTree $MiniAppRepo
    Assert-ValidPriceJson $SourceFile

    $backup = New-TemporaryFile
    Copy-Item -LiteralPath $TargetFile -Destination $backup.FullName -Force

    $sourceHashBefore = Get-Sha256 $SourceFile
    $targetHashBefore = Get-Sha256 $TargetFile
    if ($sourceHashBefore -eq $targetHashBefore) {
        Write-Host "Цены уже синхронизированы"
        return
    }

    Write-Step "Копирую цены из бота в Mini App"
    Copy-Item -LiteralPath $SourceFile -Destination $TargetFile -Force
    $copied = $true

    $sourceHashAfter = Get-Sha256 $SourceFile
    $targetHashAfter = Get-Sha256 $TargetFile
    if ($sourceHashAfter -ne $targetHashAfter) {
        Copy-Item -LiteralPath $backup.FullName -Destination $TargetFile -Force
        throw "SHA256 не совпадает после копирования. Резервная копия восстановлена."
    }

    Write-Step "Проверяю Git Mini App"
    & $script:GitExe -C $MiniAppRepo add $TargetGitPath
    if ($LASTEXITCODE -ne 0) {
        throw "Не удалось добавить файл цен в Git."
    }

    $staged = Invoke-Git -Repo $MiniAppRepo -GitArgs @("diff", "--cached", "--name-only")
    if (($staged | Measure-Object).Count -ne 1 -or $staged -ne $TargetGitPath) {
        Invoke-Git -Repo $MiniAppRepo -GitArgs @("reset", "--", $TargetGitPath) | Out-Null
        Copy-Item -LiteralPath $backup.FullName -Destination $TargetFile -Force
        throw "В commit попал не только файл цен. Резервная копия восстановлена."
    }

    Invoke-Git -Repo $MiniAppRepo -GitArgs @("diff", "--check") | Out-Null
    Invoke-Git -Repo $MiniAppRepo -GitArgs @("commit", "-m", "Sync Mini App prices from bot") | Out-Null
    Invoke-Git -Repo $MiniAppRepo -GitArgs @("push", "origin", "main") | Out-Null

    Write-Host "Готово: цены Mini App синхронизированы, commit создан и отправлен в origin/main."
}
catch {
    if ($copied -and $backup -and (Test-Path -LiteralPath $backup.FullName)) {
        Copy-Item -LiteralPath $backup.FullName -Destination $TargetFile -Force
        try {
            Invoke-Git -Repo $MiniAppRepo -GitArgs @("reset", "--", $TargetGitPath) | Out-Null
        }
        catch {
        }
    }
    Write-Error $_
    exit 1
}
finally {
    if ($backup -and (Test-Path -LiteralPath $backup.FullName)) {
        Remove-Item -LiteralPath $backup.FullName -Force
    }
}



