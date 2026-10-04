param([Parameter(Mandatory = $true)][int]$ChromeProcessId)
$ErrorActionPreference = 'Stop'
$result = @{ window_found = $false; extension_seen = $false; domain_seen = $false; allow_found = $false; invoked = $false }
try {
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    $ownedCondition = [System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::ProcessIdProperty, $ChromeProcessId)
    $deadline = [DateTime]::UtcNow.AddSeconds(12)
    while ([DateTime]::UtcNow -lt $deadline -and -not $result.invoked) {
        # Never scan the whole desktop recursively or invoke another process's controls.
        $ownedWindows = [System.Windows.Automation.AutomationElement]::RootElement.FindAll(
            [System.Windows.Automation.TreeScope]::Children, $ownedCondition)
        foreach ($ownedWindow in $ownedWindows) {
            $result.window_found = $true
            $controls = $ownedWindow.FindAll([System.Windows.Automation.TreeScope]::Descendants,
                [System.Windows.Automation.Condition]::TrueCondition)
            $extensionSeen = $false
            $domainSeen = $false
            $allowControl = $null
            foreach ($control in $controls) {
                $label = $control.Current.Name
                if ($label -like '*Cookie HTTP Seeder*') { $extensionSeen = $true }
                if ($label -match '(^|[^a-z0-9.-])(?:\*\.)?example\.com([^a-z0-9.-]|$)') { $domainSeen = $true }
                if ($control.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button -and
                    $label -in @('Allow', 'Add permissions', '允许', '允许访问') -and
                    $control.Current.IsEnabled -and -not $control.Current.IsOffscreen) {
                    $allowControl = $control
                }
            }
            $result.extension_seen = $result.extension_seen -or $extensionSeen
            $result.domain_seen = $result.domain_seen -or $domainSeen
            $result.allow_found = $result.allow_found -or ($null -ne $allowControl)
            if ($extensionSeen -and $domainSeen -and $null -ne $allowControl) {
                $pattern = $allowControl.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
                ([System.Windows.Automation.InvokePattern]$pattern).Invoke()
                $result.invoked = $true
                break
            }
        }
        if (-not $result.invoked) { Start-Sleep -Milliseconds 200 }
    }
} catch {
    $result.error_code = 'native_uia_unavailable'
}
$result | ConvertTo-Json -Compress
