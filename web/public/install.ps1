# CompactCode installer for Windows.
#
#   PowerShell:  irm https://compactcode.vercel.app/install.ps1 | iex
#   cmd:         powershell -ExecutionPolicy ByPass -c "irm https://compactcode.vercel.app/install.ps1 | iex"
#
# What it does, in order:
#   1. installs uv from Astral (https://docs.astral.sh/uv/) into your user folder, if you do not have it;
#   2. reads the latest CompactCode release from GitHub;
#   3. downloads the wheel and SHA256SUMS, and stops unless the checksum matches;
#   4. installs CompactCode as a uv tool, with its own Python 3.12, and puts `compactcode` on your PATH.
# No admin rights. Remove it with: uv tool uninstall compactcode
#
# Options (environment variables):
#   COMPACTCODE_VERSION=0.2.0       install this version instead of the latest
#   COMPACTCODE_RELEASE_URL=<url>   read release JSON from here (for testing)
#   COMPACTCODE_NO_MODIFY_PATH=1    do not add uv's tool folder to your user PATH

function Install-CompactCode {
    $ErrorActionPreference = 'Stop'
    $ProgressPreference = 'SilentlyContinue'
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    } catch {}

    # `exit` would close the window of someone who ran `irm | iex`, so failures return instead.
    function Fail([string]$Message) {
        Write-Host "CompactCode was not installed: $Message" -ForegroundColor Red
    }

    # Windows PowerShell 5.1 turns a native program's stderr (uv's progress lines) into errors, which 'Stop' makes
    # fatal. Run native programs with 'Continue', keep their output in a log, and judge them by exit code only.
    function Invoke-Native([string]$Exe, [string[]]$Arguments, [string]$Log) {
        $previous = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            & $Exe @Arguments *>&1 | ForEach-Object { "$_" } | Out-File -FilePath $Log -Encoding utf8 -Append
            return $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $previous
        }
    }

    $repo = 'amaltomajith/UntitledFYProject'
    if ($env:COMPACTCODE_RELEASE_URL) {
        $api = $env:COMPACTCODE_RELEASE_URL
    } elseif ($env:COMPACTCODE_VERSION) {
        $api = "https://api.github.com/repos/$repo/releases/tags/v$($env:COMPACTCODE_VERSION.TrimStart('v'))"
    } else {
        $api = "https://api.github.com/repos/$repo/releases/latest"
    }
    $headers = @{ 'User-Agent' = 'compactcode-installer' }
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ("compactcode-" + [Guid]::NewGuid().ToString('N').Substring(0, 12))
    New-Item -ItemType Directory -Path $tmp -Force | Out-Null

    try {
        # 1. uv
        function Find-Uv {
            $found = Get-Command uv -ErrorAction SilentlyContinue
            if ($found) { return $found.Source }
            foreach ($candidate in @(
                    (Join-Path $env:USERPROFILE '.local\bin\uv.exe'),
                    (Join-Path $env:USERPROFILE '.cargo\bin\uv.exe'))) {
                if (Test-Path $candidate) { return $candidate }
            }
            return $null
        }
        $uv = Find-Uv
        if ($uv) {
            Write-Host "Using uv at $uv"
        } else {
            Write-Host 'Installing uv from Astral (it manages Python for CompactCode)...'
            $uvScript = Join-Path $tmp 'uv-install.ps1'
            try {
                Invoke-WebRequest -UseBasicParsing -Uri 'https://astral.sh/uv/install.ps1' -OutFile $uvScript -Headers $headers
            } catch {
                return Fail 'could not download the uv installer.'
            }
            $null = Invoke-Native 'powershell' @('-NoProfile', '-ExecutionPolicy', 'ByPass', '-File', $uvScript) (Join-Path $tmp 'uv-install.log')
            $uv = Find-Uv
            if (-not $uv) { return Fail 'uv was installed but cannot be found. Open a new terminal and run this again.' }
        }

        # 2. the release
        Write-Host 'Finding the latest CompactCode release...'
        try {
            $release = Invoke-RestMethod -UseBasicParsing -Uri $api -Headers $headers
        } catch {
            return Fail "could not read the release from GitHub ($api)."
        }
        $wheelAsset = $release.assets | Where-Object { $_.name -match '^compactcode-[\w.]+-py3-none-any\.whl$' } | Select-Object -First 1
        $sumsAsset = $release.assets | Where-Object { $_.name -eq 'SHA256SUMS' } | Select-Object -First 1
        if (-not $wheelAsset) { return Fail 'the release has no CompactCode wheel.' }
        if (-not $sumsAsset) { return Fail 'the release has no SHA256SUMS, so the download cannot be checked.' }
        $name = $wheelAsset.name
        $version = $name -replace '^compactcode-(.+)-py3-none-any\.whl$', '$1'

        # 3. download and check
        Write-Host "Downloading CompactCode $version..."
        $wheel = Join-Path $tmp $name
        $sums = Join-Path $tmp 'SHA256SUMS'
        try {
            Invoke-WebRequest -UseBasicParsing -Uri $wheelAsset.browser_download_url -OutFile $wheel -Headers $headers
            Invoke-WebRequest -UseBasicParsing -Uri $sumsAsset.browser_download_url -OutFile $sums -Headers $headers
        } catch {
            return Fail 'could not download the release files.'
        }
        $want = $null
        foreach ($line in Get-Content -LiteralPath $sums) {
            $parts = $line.Trim() -split '\s+', 2
            if ($parts.Count -eq 2 -and $parts[1].TrimStart('*') -eq $name) { $want = $parts[0].ToLowerInvariant(); break }
        }
        if (-not $want) { return Fail "SHA256SUMS does not list $name." }
        $got = (Get-FileHash -LiteralPath $wheel -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($got -ne $want) { return Fail 'the download does not match its published SHA-256. Nothing was installed.' }
        Write-Host 'Checksum matches.'

        # 4. install
        Write-Host 'Installing (uv fetches Python 3.12 if it needs to)...'
        $uri = ([Uri]$wheel).AbsoluteUri
        $log = Join-Path $tmp 'uv.log'
        $install = @('tool', 'install', '--force', '--python', '3.12', "compactcode[web] @ $uri")
        $code = Invoke-Native $uv $install $log
        if ($code -ne 0) {
            # An earlier install that was cut off leaves an environment uv will not reuse: remove it and try once more.
            $null = Invoke-Native $uv @('tool', 'uninstall', 'compactcode') $log
            $code = Invoke-Native $uv $install $log
        }
        if ($code -ne 0) {
            Get-Content -LiteralPath $log | Write-Host
            return Fail 'uv could not install it (output above).'
        }
        if (-not $env:COMPACTCODE_NO_MODIFY_PATH) { $null = Invoke-Native $uv @('tool', 'update-shell') (Join-Path $tmp 'path.log') }
        $binLog = Join-Path $tmp 'bin.log'
        $null = Invoke-Native $uv @('tool', 'dir', '--bin') $binLog
        $bin = (Get-Content -LiteralPath $binLog -ErrorAction SilentlyContinue | Where-Object { $_ -match '^[A-Za-z]:\\' } | Select-Object -First 1)
        if ($bin -and ($env:Path -split ';') -notcontains $bin) { $env:Path = "$bin;$env:Path" }

        Write-Host ''
        Write-Host "CompactCode $version is installed." -ForegroundColor Green
        Write-Host 'Open a new terminal in your project and type: compactcode'
        Write-Host 'It opens in your browser. To remove it: uv tool uninstall compactcode'
    } finally {
        Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Install-CompactCode
