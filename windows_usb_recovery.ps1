<#
.SYNOPSIS
Automatically recovers USB devices that Windows failed to start, such as a
webcam microphone reporting "Device Descriptor Request Failed" (Code 43).

.DESCRIPTION
Run once from an elevated PowerShell with -Install. This copies the script to
a folder only administrators can change and registers a SYSTEM scheduled task
that runs a repair pass:
  - 30 seconds after Windows starts,
  - after the PC resumes from sleep,
  - whenever Windows logs that a device had a problem starting, and
  - on demand from RedWhisper when it finds no usable microphone.

A repair pass restarts each failed USB device, then removes it and rescans
hardware, and as a last resort restarts the USB hub it is connected to. It
does nothing when every USB device is healthy, and it stops after three passes
in ten minutes so a permanently broken device cannot cause a restart loop.

  -Check      List failed USB devices (no administrator rights needed).
  -Install    Register the scheduled task (administrator).
  -Uninstall  Remove the scheduled task and its folder (administrator).
  (no switch) Run one repair pass now (administrator).
#>
[CmdletBinding()]
param(
    [switch]$Check,
    [switch]$Install,
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'

$TaskFolder = 'RedWhisper'
$TaskName = 'Repair USB devices'
$InstallDir = Join-Path $env:ProgramData 'RedWhisperUsbRecovery'
$InstalledScript = Join-Path $InstallDir 'windows_usb_recovery.ps1'
$LogPath = Join-Path $InstallDir 'usb-recovery.log'
$RunHistoryPath = Join-Path $InstallDir 'recent-runs.txt'
# Code 10: device cannot start. Code 43: device reported a failure, including
# "Device Descriptor Request Failed". A port reset fixes both; other codes
# (missing drivers, disabled by the user) are left alone.
$RecoverableProblemCodes = @(10, 43)
$MaxRunsPerWindow = 3
$RunWindow = [TimeSpan]::FromMinutes(10)

function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Write-Log([string]$Message) {
    $line = '{0:yyyy-MM-dd HH:mm:ss}  {1}' -f (Get-Date), $Message
    Write-Output $line
    if (-not (Test-Path $InstallDir)) {
        return
    }
    if ((Test-Path $LogPath) -and (Get-Item $LogPath).Length -gt 256KB) {
        Move-Item $LogPath "$LogPath.old" -Force
    }
    Add-Content -Path $LogPath -Value $line -Encoding utf8
}

function Get-FailedUsbDevices {
    $filter = ($RecoverableProblemCodes | ForEach-Object { "ConfigManagerErrorCode = $_" }) -join ' OR '
    @(Get-CimInstance Win32_PnPEntity -Filter $filter |
        Where-Object { $_.PNPDeviceID -like 'USB\*' })
}

function Get-ParentDevice([string]$InstanceId) {
    try {
        (Get-PnpDeviceProperty -InstanceId $InstanceId -KeyName 'DEVPKEY_Device_Parent').Data
    } catch {
        $null
    }
}

function Invoke-PnpUtil([string[]]$Arguments) {
    # Windows PowerShell turns native stderr into errors; keep going instead.
    $ErrorActionPreference = 'Continue'
    $output = & pnputil.exe @Arguments 2>&1 | Out-String
    Write-Log ("pnputil {0} -> exit {1}: {2}" -f ($Arguments -join ' '), $LASTEXITCODE, $output.Trim())
}

function Enter-RunWindow {
    $now = Get-Date
    $recent = @()
    if (Test-Path $RunHistoryPath) {
        $recent = @(Get-Content $RunHistoryPath | ForEach-Object {
            $stamp = [datetime]::MinValue
            if ([datetime]::TryParse($_, [ref]$stamp) -and ($now - $stamp) -lt $RunWindow) {
                $stamp
            }
        })
    }
    if ($recent.Count -ge $MaxRunsPerWindow) {
        return $false
    }
    $recent += $now
    Set-Content -Path $RunHistoryPath -Value ($recent | ForEach-Object { $_.ToString('o') }) -Encoding utf8
    return $true
}

function Invoke-Repair {
    $failed = Get-FailedUsbDevices
    if ($failed.Count -eq 0) {
        return
    }
    if (-not (Enter-RunWindow)) {
        Write-Log "Skipped: already ran $MaxRunsPerWindow times in the last $($RunWindow.TotalMinutes) minutes."
        return
    }
    Write-Log ("Failed USB devices: " + (($failed | ForEach-Object { "$($_.Name) [$($_.PNPDeviceID), code $($_.ConfigManagerErrorCode)]" }) -join '; '))
    $parents = @{}
    foreach ($device in $failed) {
        $parents[$device.PNPDeviceID] = Get-ParentDevice $device.PNPDeviceID
    }

    $steps = @(
        @{ Name = 'restart device'; Wait = 6; Action = {
            param($id) Invoke-PnpUtil @('/restart-device', $id) } },
        @{ Name = 'remove device and rescan'; Wait = 8; Action = {
            param($id) Invoke-PnpUtil @('/remove-device', $id); Invoke-PnpUtil @('/scan-devices') } },
        @{ Name = 'restart parent USB hub'; Wait = 10; Action = {
            param($id)
            $parent = $parents[$id]
            if (-not $parent) { Write-Log "No parent hub found for $id" }
            elseif ($restartedHubs.Add($parent)) { Invoke-PnpUtil @('/restart-device', $parent) } } }
    )
    $restartedHubs = [System.Collections.Generic.HashSet[string]]::new()
    foreach ($step in $steps) {
        Write-Log "Trying: $($step.Name)"
        foreach ($device in $failed) {
            & $step.Action $device.PNPDeviceID
        }
        Start-Sleep -Seconds $step.Wait
        $failed = Get-FailedUsbDevices
        if ($failed.Count -eq 0) {
            Write-Log "Recovered after: $($step.Name)"
            return
        }
    }
    Write-Log ("Still failing: " + (($failed | ForEach-Object { $_.Name }) -join '; ') +
        ". Unplug the device and connect it to another USB port.")
}

function Install-Recovery {
    if (-not (Test-Administrator)) {
        throw 'Run this from an elevated PowerShell (Run as administrator) to install.'
    }
    # The task runs this script as SYSTEM, so it must live where standard
    # users cannot replace it.
    if (Test-Path $InstallDir) {
        & takeown.exe /F $InstallDir /R /A /D Y | Out-Null
        & icacls.exe $InstallDir /reset /T /C /Q | Out-Null
        Remove-Item $InstallDir -Recurse -Force
    }
    New-Item -ItemType Directory -Path $InstallDir | Out-Null
    & icacls.exe $InstallDir /setowner '*S-1-5-32-544' /Q | Out-Null
    & icacls.exe $InstallDir /inheritance:r /grant:r `
        '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-32-545:(OI)(CI)RX' /Q | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Could not secure $InstallDir"
    }
    Copy-Item -Path $PSCommandPath -Destination $InstalledScript

    $action = 'powershell.exe'
    $arguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File &quot;$InstalledScript&quot;"
    $taskXml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Author>RedWhisper</Author>
    <Description>Resets USB devices (such as a webcam microphone) that Windows failed to start. Installed by windows_usb_recovery.ps1.</Description>
  </RegistrationInfo>
  <Triggers>
    <BootTrigger>
      <Enabled>true</Enabled>
      <Delay>PT30S</Delay>
    </BootTrigger>
    <EventTrigger>
      <Enabled>true</Enabled>
      <Delay>PT5S</Delay>
      <Subscription>&lt;QueryList&gt;&lt;Query Id="0" Path="Microsoft-Windows-Kernel-PnP/Configuration"&gt;&lt;Select Path="Microsoft-Windows-Kernel-PnP/Configuration"&gt;*[System[EventID=411]]&lt;/Select&gt;&lt;/Query&gt;&lt;/QueryList&gt;</Subscription>
    </EventTrigger>
    <EventTrigger>
      <Enabled>true</Enabled>
      <Delay>PT10S</Delay>
      <Subscription>&lt;QueryList&gt;&lt;Query Id="0" Path="System"&gt;&lt;Select Path="System"&gt;*[System[Provider[@Name='Microsoft-Windows-Power-Troubleshooter'] and EventID=1]]&lt;/Select&gt;&lt;/Query&gt;&lt;/QueryList&gt;</Subscription>
    </EventTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>S-1-5-18</UserId>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>PT5M</ExecutionTimeLimit>
    <Priority>5</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>$action</Command>
      <Arguments>$arguments</Arguments>
    </Exec>
  </Actions>
</Task>
"@
    Register-ScheduledTask -TaskPath "\$TaskFolder\" -TaskName $TaskName -Xml $taskXml -Force | Out-Null

    # Let signed-in users start (but not change) the task, so RedWhisper can
    # request a repair without administrator rights.
    $service = New-Object -ComObject 'Schedule.Service'
    $service.Connect()
    $task = $service.GetFolder("\$TaskFolder").GetTask($TaskName)
    $task.SetSecurityDescriptor('D:(A;;FA;;;SY)(A;;FA;;;BA)(A;;GRGX;;;AU)', 0)

    Write-Log "Installed scheduled task \$TaskFolder\$TaskName"
    Invoke-Repair
}

function Uninstall-Recovery {
    if (-not (Test-Administrator)) {
        throw 'Run this from an elevated PowerShell (Run as administrator) to uninstall.'
    }
    Unregister-ScheduledTask -TaskPath "\$TaskFolder\" -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    if (Test-Path $InstallDir) {
        Remove-Item $InstallDir -Recurse -Force
    }
    Write-Output "Removed scheduled task \$TaskFolder\$TaskName"
}

if ($Check) {
    $failed = Get-FailedUsbDevices
    if ($failed.Count -eq 0) {
        Write-Output 'All USB devices started normally.'
    } else {
        $failed | ForEach-Object { Write-Output "$($_.Name) [$($_.PNPDeviceID), code $($_.ConfigManagerErrorCode)]" }
    }
} elseif ($Install) {
    Install-Recovery
} elseif ($Uninstall) {
    Uninstall-Recovery
} else {
    if (-not (Test-Administrator)) {
        throw 'A repair pass needs administrator rights. Use -Check to only list failed devices.'
    }
    Invoke-Repair
}
