# Starts the Telegram listener and the scheduler, each in its own window.
# Loads your saved (setx) variables first, so it works even from an old terminal.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$loadEnv = @"
foreach (`$n in 'TELEGRAM_BOT_TOKEN','LLM_API_KEY','ANTHROPIC_API_KEY','SMTP_USER','SMTP_PASSWORD','SMTP_HOST','SMTP_PORT','SMTP_FROM','TAVILY_API_KEY','BRAVE_API_KEY') {
  `$v = [Environment]::GetEnvironmentVariable(`$n,'User'); if (`$v) { Set-Item "env:`$n" `$v }
}
Set-Location '$here'
"@
Start-Process powershell -ArgumentList '-NoExit','-Command',($loadEnv + "`n`$Host.UI.RawUI.WindowTitle='Findjobs: Telegram'; python poll_telegram.py")
Start-Process powershell -ArgumentList '-NoExit','-Command',($loadEnv + "`n`$Host.UI.RawUI.WindowTitle='Findjobs: Scheduler'; python scheduler.py")
