param(
    [string]$MiniAppRepo = "C:\Users\1\Documents\Codex\2026-07-09\telegram-python-1-user-id-2\esim-miniapp"
)

$ErrorActionPreference = "Stop"

$BotRepo = Resolve-Path (Join-Path $PSScriptRoot "..")
$SourceFile = Join-Path $BotRepo "unlimited_catalog.json"
$TargetFile = Join-Path $MiniAppRepo "src\data\unlimited_catalog.json"

function Write-Step {
    param([string]$Message)
    Write-Host "==> $Message"
}

function Assert-Repo {
    param([string]$Repo)

    if (-not (Test-Path -LiteralPath $Repo -PathType Container)) {
        throw "Repo not found: $Repo"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $Repo ".git"))) {
        throw "Directory is not a Git repo: $Repo"
    }
}

function Assert-IntegerValue {
    param(
        [object]$Value,
        [string]$Label
    )

    if ($Value -is [bool] -or $null -eq $Value) {
        throw "Invalid integer value for $($Label): $Value"
    }
    if (([string]$Value) -notmatch '^\d+$') {
        throw "Invalid integer value for $($Label): $Value"
    }
    return [int64]$Value
}

function Assert-ValidUnlimitedJson {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Unlimited catalog not found: $Path"
    }

    $json = Get-Content -Raw -Encoding UTF8 -LiteralPath $Path | ConvertFrom-Json
    if (-not $json.plans -or -not ($json.plans -is [array])) {
        throw "unlimited_catalog.json must contain a non-empty plans array."
    }

    $plans = @($json.plans)
    $excluded = @($json.excluded_without_1_or_2gb)

    if ($plans.Count -le 0) {
        throw "Unlimited catalog has no plans."
    }
    if ($null -eq $json.excluded_without_1_or_2gb) {
        throw "Missing excluded_without_1_or_2gb array."
    }

    $seenKeys = @{}
    foreach ($plan in $plans) {
        if (-not $plan.supplier_key) { throw "Found plan without supplier_key." }
        if ($seenKeys.ContainsKey($plan.supplier_key)) { throw "Duplicate supplier_key: $($plan.supplier_key)" }
        $seenKeys[$plan.supplier_key] = $true

        if ($plan.supplier_key -in @("Russia", "Guadeloupe")) {
            throw "Forbidden direction found in catalog: $($plan.supplier_key)"
        }
        if (-not $plan.display_name) { throw "Missing display_name for $($plan.supplier_key)." }
        if ($plan.direction_type -notin @("country", "region")) {
            throw "Invalid direction_type for $($plan.supplier_key): $($plan.direction_type)"
        }
        if ($plan.supplier_class -notin @("Economy", "Comfort", "Premium")) {
            throw "Invalid supplier_class for $($plan.supplier_key): $($plan.supplier_class)"
        }

        $daily = Assert-IntegerValue $plan.daily_high_speed_gb "daily_high_speed_gb for $($plan.supplier_key)"
        if ($daily -notin @(1, 2)) {
            throw "Invalid daily_high_speed_gb for $($plan.supplier_key): $daily"
        }
        if (-not $plan.post_limit_speed) { throw "Missing post_limit_speed for $($plan.supplier_key)." }
        if (-not $plan.retail_prices_rub) { throw "Missing retail_prices_rub for $($plan.supplier_key)." }

        $previousPrice = 0
        foreach ($day in 1..30) {
            $key = [string]$day
            $price = Assert-IntegerValue $plan.retail_prices_rub.$key "price for $($plan.supplier_key), $day days"
            if ($price -le 0) {
                throw "Non-positive price for $($plan.supplier_key), $day days: $price"
            }
            if ($price -lt $previousPrice) {
                throw "Price decreases for $($plan.supplier_key), $day days."
            }
            $previousPrice = $price
        }
    }

    Write-Host "Validated plans: $($plans.Count)"
    Write-Host "Excluded directions: $($excluded.Count)"
}

function Get-Sha256 {
    param([string]$Path)
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash
}

Write-Step "Checking repos"
Assert-Repo $BotRepo
Assert-Repo $MiniAppRepo

Write-Step "Validating unlimited JSON"
Assert-ValidUnlimitedJson $SourceFile

Write-Step "Copying JSON to Mini App"
Copy-Item -LiteralPath $SourceFile -Destination $TargetFile -Force

$sourceHash = Get-Sha256 $SourceFile
$targetHash = Get-Sha256 $TargetFile
if ($sourceHash -ne $targetHash) {
    throw "SHA256 mismatch after copy. Bot: $sourceHash Mini App: $targetHash"
}

Write-Host "Done: unlimited_catalog.json synchronized."
Write-Host "SHA256: $sourceHash"
