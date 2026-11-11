#Requires -Version 7
<#
    Acceptance A10: the report's measures reconcile to SQL.

    With RetailLoyalty.pbip open in Power BI Desktop, this script:
      1. connects to the Analysis Services engine behind Desktop (127.0.0.1 only);
      2. refreshes the model from the exports folder, overriding the ExportFolder parameter for this refresh only,
         so the project files never hold a machine-specific path;
      3. evaluates the DAX of every check in reconciliation.json, under the stated RLS role where there is one;
      4. compares each result with the SQL value written by `loyalty reference`.

    Run `loyalty export` and then `loyalty reference` first (in the container or with uv). Exits 1 on any mismatch.
    The ADOMD.NET client is downloaded once from NuGet into bi/.tools (ignored by Git).
#>
param(
    [string]$Exports = (Join-Path $PSScriptRoot "..\exports"),
    [string]$AdomdVersion = "19.84.1",
    [switch]$SkipRefresh
)
$ErrorActionPreference = "Stop"

$package = "microsoft.analysisservices.adomdclient.netcore.retail.amd64"
$tools = Join-Path $PSScriptRoot ".tools\adomd-$AdomdVersion"
$dll = Join-Path $tools "lib\netcoreapp3.0\Microsoft.AnalysisServices.AdomdClient.dll"
if (-not (Test-Path $dll)) {
    New-Item -ItemType Directory -Force $tools | Out-Null
    $nupkg = Join-Path $tools "package.zip"
    Invoke-WebRequest "https://api.nuget.org/v3-flatcontainer/$package/$AdomdVersion/$package.$AdomdVersion.nupkg" -OutFile $nupkg
    Expand-Archive $nupkg $tools -Force
    $signer = (Get-AuthenticodeSignature $dll).SignerCertificate.Subject
    if ($signer -notmatch "O=Microsoft Corporation") { throw "ADOMD client is not signed by Microsoft: $signer" }
}
Add-Type -Path $dll

function Invoke-Engine([int]$Port, [string]$Text, [string]$Role, [switch]$NonQuery) {
    $connection = "Data Source=localhost:$Port"
    if ($Role) { $connection += ";Roles=$Role" }
    $c = [Microsoft.AnalysisServices.AdomdClient.AdomdConnection]::new($connection)
    $c.Open()
    try {
        $cmd = $c.CreateCommand(); $cmd.CommandText = $Text; $cmd.CommandTimeout = 600
        if ($NonQuery) { [void]$cmd.ExecuteNonQuery(); return }
        # ADOMD.NET has no ExecuteScalar: read the first cell.
        $reader = $cmd.ExecuteReader()
        try { if ($reader.Read()) { return $reader.GetValue(0) } else { return $null } } finally { $reader.Close() }
    } finally { $c.Close() }
}

function ConvertTo-Plain($value) {
    if ($null -eq $value -or $value -is [DBNull]) { return $null }
    if ($value -is [datetime]) { return $value.ToString("yyyy-MM-dd") }
    if ($value -is [string]) { return $value }
    return [double]$value
}

function Test-Equal($actual, $expected) {
    if ($null -eq $expected -or $null -eq $actual) { return ($null -eq $expected) -and ($null -eq $actual) }
    if ($expected -is [string] -or $actual -is [string]) { return "$actual" -eq "$expected" }
    $expected = [double]$expected
    # Money and counts to the penny; rates (below 1) to 1e-9.
    $tolerance = if ([math]::Abs($expected) -ge 1) { 0.005 } else { 1e-9 }
    return [math]::Abs($actual - $expected) -le $tolerance
}

# Every open Power BI Desktop has its own engine: use the one whose model is this project's.
$port = $null
foreach ($engine in Get-Process msmdsrv -ErrorAction SilentlyContinue) {
    $candidate = (Get-NetTCPConnection -OwningProcess $engine.Id -State Listen -ErrorAction SilentlyContinue |
        Where-Object LocalAddress -eq "127.0.0.1" | Select-Object -First 1).LocalPort
    if (-not $candidate) { continue }
    $tables = Invoke-Engine $candidate 'select [Name] from $SYSTEM.TMSCHEMA_TABLES where [Name] = ''fct_member_month'''
    if ($tables) { $port = $candidate; break }
}
if (-not $port) { throw "No open Power BI Desktop holds this model: open bi/RetailLoyalty.pbip first." }
$database = Invoke-Engine $port 'select [CATALOG_NAME] from $SYSTEM.DBSCHEMA_CATALOGS'

$exportsPath = (Resolve-Path $Exports).Path
$reference = Get-Content (Join-Path $exportsPath "reference_values.json") -Raw | ConvertFrom-Json -AsHashtable
$checks = (Get-Content (Join-Path $PSScriptRoot "reconciliation.json") -Raw | ConvertFrom-Json).checks

if (-not $SkipRefresh) {
    $parameter = '"' + $exportsPath + '" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]'
    $refresh = @{ refresh = @{ type = "full"; objects = @(@{ database = $database })
            overrides = @(@{ expressions = @(@{ originalObject = @{ database = $database; expression = "ExportFolder" }; expression = $parameter }) }) } }
    $seconds = (Measure-Command { Invoke-Engine $port ($refresh | ConvertTo-Json -Depth 8 -Compress) -NonQuery }).TotalSeconds
    Write-Host ("Refreshed from {0} in {1:N0} s" -f $exportsPath, $seconds)
}

$failures = 0
$rows = foreach ($check in $checks) {
    $actual = ConvertTo-Plain (Invoke-Engine $port "EVALUATE ROW ( ""v"", $($check.dax) )" -Role $check.role)
    $expected = $reference[$check.name]
    $ok = Test-Equal $actual $expected
    if (-not $ok) { $failures++ }
    [pscustomobject]@{ Check = $check.name; Role = $check.role; DAX = $actual; SQL = $expected; Result = $(if ($ok) { "match" } else { "MISMATCH" }) }
}

"| Check | Role | Power BI (DAX) | Reference (SQL) | Result |"
"| --- | --- | --- | --- | --- |"
foreach ($r in $rows) {
    $fmt = { param($v) if ($null -eq $v) { "blank" } elseif ($v -is [double]) { "{0:0.######}" -f $v } else { "$v" } }
    "| $($r.Check) | $($r.Role) | $(& $fmt $r.DAX) | $(& $fmt $r.SQL) | $($r.Result) |"
}
""
"$($checks.Count - $failures) of $($checks.Count) checks match."
if ($failures) { exit 1 }
