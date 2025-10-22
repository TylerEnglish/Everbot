$ver  = '3.0.11'
$root = 'C:\Tools\xdelta3'
New-Item -ItemType Directory -Path $root -Force | Out-Null

# Download the official Windows zip
$uri = "https://github.com/jmacd/xdelta-gpl/releases/download/v$ver/xdelta3-$ver-x86_64.exe.zip"
$zip = Join-Path $root "xdelta3-$ver.zip"
Invoke-WebRequest -Uri $uri -OutFile $zip

# Unzip (zip contains the exe)
Expand-Archive -Path $zip -DestinationPath $root -Force

# Rename/copy the extracted exe to a stable name
$exe = Get-ChildItem $root -Filter '*xdelta3*.exe' -File | Select-Object -First 1
Copy-Item $exe.FullName (Join-Path $root 'xdelta3.exe') -Force

# Remove “downloaded from web” warning just in case
Unblock-File (Join-Path $root 'xdelta3.exe')

# Point Everabot to it (current session + persist for your account)
$env:EVERABOT_XDELTA3 = "$root\xdelta3.exe"
[Environment]::SetEnvironmentVariable('EVERABOT_XDELTA3', "$root\xdelta3.exe", 'User')

# (Optional) Also add folder to your user PATH so you can just run `xdelta3`
$path = [Environment]::GetEnvironmentVariable('Path','User')
if ($path -notlike "*$root*") {
  [Environment]::SetEnvironmentVariable('Path', "$path;$root", 'User')
}

# Sanity check
& "$env:EVERABOT_XDELTA3" -V  # or -h
