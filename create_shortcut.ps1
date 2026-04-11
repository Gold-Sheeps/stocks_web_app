$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut($env:USERPROFILE + "\Desktop\StocksApp.lnk")
$s.TargetPath = "C:\Users\o_van\Desktop\stocks_web_app\start_services_visible.cmd"
$s.WorkingDirectory = "C:\Users\o_van\Desktop\stocks_web_app"
$s.Save()
Write-Host "Shortcut updated!"
