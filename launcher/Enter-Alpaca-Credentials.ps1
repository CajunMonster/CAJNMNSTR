#requires -Version 5.1

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.Application]::EnableVisualStyles()

$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$envPath = Join-Path $projectRoot '.env.local'
$configCheckPath = Join-Path $projectRoot '.venv\Scripts\cajnmnstr.exe'

function ConvertTo-DotenvLiteral {
    param([Parameter(Mandatory = $true)][string]$Value)

    $escaped = $Value.Replace('\', '\\').Replace("'", "\'")
    return "'$escaped'"
}

function Test-DisabledValue {
    param([Parameter(Mandatory = $true)][string]$Value)

    $normalized = $Value.Trim().Trim('"').Trim("'").ToLowerInvariant()
    return $normalized -in @('false', '0', 'no', 'off')
}

function Set-SafeStatus {
    param(
        [Parameter(Mandatory = $true)][string]$Text,
        [Parameter(Mandatory = $true)][System.Drawing.Color]$Color
    )

    $statusLabel.ForeColor = $Color
    $statusLabel.Text = $Text
}

$form = New-Object System.Windows.Forms.Form
$form.Text = 'CAJNMNSTR - Alpaca Paper Credentials'
$form.ClientSize = New-Object System.Drawing.Size(600, 355)
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.MinimizeBox = $false
$form.TopMost = $true
$form.BackColor = [System.Drawing.Color]::FromArgb(12, 13, 15)
$form.ForeColor = [System.Drawing.Color]::FromArgb(224, 207, 166)
$form.Font = New-Object System.Drawing.Font('Segoe UI', 10)

$titleLabel = New-Object System.Windows.Forms.Label
$titleLabel.Text = 'LOCAL ALPACA PAPER CREDENTIAL ENTRY'
$titleLabel.Location = New-Object System.Drawing.Point(28, 22)
$titleLabel.Size = New-Object System.Drawing.Size(545, 28)
$titleLabel.Font = New-Object System.Drawing.Font('Segoe UI Semibold', 15)
$titleLabel.ForeColor = [System.Drawing.Color]::FromArgb(214, 164, 72)
$form.Controls.Add($titleLabel)

$safetyLabel = New-Object System.Windows.Forms.Label
$safetyLabel.Text = "Enter both values only in this local window. Fields are masked. No network call or order will be made. Execution must remain disabled."
$safetyLabel.Location = New-Object System.Drawing.Point(30, 58)
$safetyLabel.Size = New-Object System.Drawing.Size(535, 42)
$safetyLabel.ForeColor = [System.Drawing.Color]::FromArgb(180, 184, 190)
$form.Controls.Add($safetyLabel)

$apiLabel = New-Object System.Windows.Forms.Label
$apiLabel.Text = 'ALPACA_API_KEY'
$apiLabel.Location = New-Object System.Drawing.Point(30, 112)
$apiLabel.Size = New-Object System.Drawing.Size(220, 22)
$form.Controls.Add($apiLabel)

$apiBox = New-Object System.Windows.Forms.TextBox
$apiBox.Location = New-Object System.Drawing.Point(30, 136)
$apiBox.Size = New-Object System.Drawing.Size(535, 27)
$apiBox.UseSystemPasswordChar = $true
$apiBox.TabIndex = 0
$form.Controls.Add($apiBox)

$secretLabel = New-Object System.Windows.Forms.Label
$secretLabel.Text = 'ALPACA_SECRET_KEY'
$secretLabel.Location = New-Object System.Drawing.Point(30, 178)
$secretLabel.Size = New-Object System.Drawing.Size(220, 22)
$form.Controls.Add($secretLabel)

$secretBox = New-Object System.Windows.Forms.TextBox
$secretBox.Location = New-Object System.Drawing.Point(30, 202)
$secretBox.Size = New-Object System.Drawing.Size(535, 27)
$secretBox.UseSystemPasswordChar = $true
$secretBox.TabIndex = 1
$form.Controls.Add($secretBox)

$saveButton = New-Object System.Windows.Forms.Button
$saveButton.Text = 'SAVE LOCALLY AND VERIFY'
$saveButton.Location = New-Object System.Drawing.Point(30, 250)
$saveButton.Size = New-Object System.Drawing.Size(260, 40)
$saveButton.TabIndex = 2
$saveButton.BackColor = [System.Drawing.Color]::FromArgb(87, 61, 25)
$saveButton.ForeColor = [System.Drawing.Color]::White
$saveButton.FlatStyle = 'Flat'
$form.Controls.Add($saveButton)

$closeButton = New-Object System.Windows.Forms.Button
$closeButton.Text = 'CLOSE WITHOUT SAVING'
$closeButton.Location = New-Object System.Drawing.Point(305, 250)
$closeButton.Size = New-Object System.Drawing.Size(260, 40)
$closeButton.TabIndex = 3
$closeButton.BackColor = [System.Drawing.Color]::FromArgb(35, 37, 41)
$closeButton.ForeColor = [System.Drawing.Color]::FromArgb(210, 210, 210)
$closeButton.FlatStyle = 'Flat'
$closeButton.Add_Click({ $form.Close() })
$form.Controls.Add($closeButton)

$statusLabel = New-Object System.Windows.Forms.Label
$statusLabel.Text = 'ALPACA CREDENTIALS: PENDING LOCAL ENTRY'
$statusLabel.Location = New-Object System.Drawing.Point(30, 306)
$statusLabel.Size = New-Object System.Drawing.Size(535, 28)
$statusLabel.Font = New-Object System.Drawing.Font('Segoe UI Semibold', 10)
$statusLabel.ForeColor = [System.Drawing.Color]::FromArgb(180, 184, 190)
$form.Controls.Add($statusLabel)

$saveButton.Add_Click({
    $apiValue = $apiBox.Text
    $secretValue = $secretBox.Text
    $writeCompleted = $false

    try {
        if ([string]::IsNullOrWhiteSpace($apiValue) -or [string]::IsNullOrWhiteSpace($secretValue)) {
            Set-SafeStatus 'ALPACA CREDENTIALS: ABSENT' ([System.Drawing.Color]::FromArgb(225, 95, 85))
            return
        }

        if ($apiValue.IndexOfAny(@([char]13, [char]10)) -ge 0 -or
            $secretValue.IndexOfAny(@([char]13, [char]10)) -ge 0) {
            Set-SafeStatus 'ALPACA CREDENTIALS: ABSENT' ([System.Drawing.Color]::FromArgb(225, 95, 85))
            return
        }

        if (-not (Test-Path -LiteralPath $envPath -PathType Leaf) -or
            -not (Test-Path -LiteralPath $configCheckPath -PathType Leaf)) {
            Set-SafeStatus 'ALPACA CREDENTIALS: ABSENT' ([System.Drawing.Color]::FromArgb(225, 95, 85))
            return
        }

        $content = [System.IO.File]::ReadAllText($envPath)
        $entryPattern = '(?m)^[ \t]*(?:export[ \t]+)?CAJNMNSTR_ENTRY_ENABLED[ \t]*=[ \t]*(?<value>[^\r\n]*)'
        $legacyEntryPattern = '(?m)^[ \t]*(?:export[ \t]+)?CAJNMNSTR_EXECUTION_ENABLED[ \t]*=[ \t]*(?<value>[^\r\n]*)'
        $apiPattern = '(?m)^(?<prefix>[ \t]*(?:export[ \t]+)?ALPACA_API_KEY[ \t]*=[ \t]*)[^\r\n]*'
        $secretPattern = '(?m)^(?<prefix>[ \t]*(?:export[ \t]+)?ALPACA_SECRET_KEY[ \t]*=[ \t]*)[^\r\n]*'

        $entryMatches = [regex]::Matches($content, $entryPattern)
        $legacyEntryMatches = [regex]::Matches($content, $legacyEntryPattern)
        $apiMatches = [regex]::Matches($content, $apiPattern)
        $secretMatches = [regex]::Matches($content, $secretPattern)

        $entryDisabled = (
            $entryMatches.Count -eq 1 -and
            (Test-DisabledValue $entryMatches[0].Groups['value'].Value)
        ) -or (
            $entryMatches.Count -eq 0 -and
            $legacyEntryMatches.Count -eq 1 -and
            (Test-DisabledValue $legacyEntryMatches[0].Groups['value'].Value)
        )

        if (-not $entryDisabled -or
            $apiMatches.Count -ne 1 -or
            $secretMatches.Count -ne 1) {
            Set-SafeStatus 'ALPACA CREDENTIALS: ABSENT' ([System.Drawing.Color]::FromArgb(225, 95, 85))
            return
        }

        $apiLiteral = ConvertTo-DotenvLiteral $apiValue
        $secretLiteral = ConvertTo-DotenvLiteral $secretValue
        $apiRegex = [regex]::new($apiPattern)
        $secretRegex = [regex]::new($secretPattern)
        $updated = $apiRegex.Replace(
            $content,
            [System.Text.RegularExpressions.MatchEvaluator]{
                param($match)
                return $match.Groups['prefix'].Value + $apiLiteral
            },
            1
        )
        $updated = $secretRegex.Replace(
            $updated,
            [System.Text.RegularExpressions.MatchEvaluator]{
                param($match)
                return $match.Groups['prefix'].Value + $secretLiteral
            },
            1
        )

        [System.IO.File]::WriteAllText(
            $envPath,
            $updated,
            [System.Text.UTF8Encoding]::new($false)
        )
        $writeCompleted = $true

        $apiBox.Clear()
        $secretBox.Clear()
        $apiValue = $null
        $secretValue = $null
        $apiLiteral = $null
        $secretLiteral = $null

        $startInfo = New-Object System.Diagnostics.ProcessStartInfo
        $startInfo.FileName = $configCheckPath
        $startInfo.Arguments = 'config-check'
        $startInfo.WorkingDirectory = $projectRoot
        $startInfo.UseShellExecute = $false
        $startInfo.CreateNoWindow = $true
        $startInfo.RedirectStandardOutput = $true
        $startInfo.RedirectStandardError = $true

        foreach ($name in @(
            'ALPACA_API_KEY',
            'ALPACA_SECRET_KEY',
            'CAJNMNSTR_EXECUTION_ENABLED',
            'CAJNMNSTR_EXECUTION_ARMED',
            'CAJNMNSTR_ENTRY_ENABLED',
            'CAJNMNSTR_POSITION_MANAGEMENT_ENABLED',
            'CAJNMNSTR_BROKER_LOCK'
        )) {
            $startInfo.EnvironmentVariables.Remove($name)
        }

        $process = New-Object System.Diagnostics.Process
        $process.StartInfo = $startInfo
        $null = $process.Start()
        $stdout = $process.StandardOutput.ReadToEnd()
        $null = $process.StandardError.ReadToEnd()
        $process.WaitForExit()

        if ($process.ExitCode -ne 0) {
            Set-SafeStatus 'ALPACA CREDENTIALS: ABSENT' ([System.Drawing.Color]::FromArgb(225, 95, 85))
            return
        }

        $check = $stdout | ConvertFrom-Json
        $stdout = $null

        if ($check.alpaca_credentials_present -eq $true -and
            $check.entry_enabled -eq $false -and
            $check.entry_armed -eq $false) {
            Set-SafeStatus 'ALPACA CREDENTIALS: PRESENT' ([System.Drawing.Color]::FromArgb(82, 191, 109))
        }
        else {
            Set-SafeStatus 'ALPACA CREDENTIALS: ABSENT' ([System.Drawing.Color]::FromArgb(225, 95, 85))
        }
    }
    catch {
        if ($writeCompleted) {
            $apiBox.Clear()
            $secretBox.Clear()
        }
        Set-SafeStatus 'ALPACA CREDENTIALS: ABSENT' ([System.Drawing.Color]::FromArgb(225, 95, 85))
    }
    finally {
        $apiValue = $null
        $secretValue = $null
    }
})

$form.AcceptButton = $saveButton
$form.CancelButton = $closeButton
$form.Add_Shown({ $apiBox.Focus() })
$null = $form.ShowDialog()
