$go = 'C:\Program Files\Go\bin'
if (-not ($env:Path -split ';' | % { $_ -eq $go } | Measure-Object -Sum).Sum) {
  $env:Path += ";$go"
}
go version
cd bot/accel/go/
go mod tidy
mkdir -Force .\bin | Out-Null
go build -o .\bin\everabot_chunk_go.exe .\cmd\chunker
.\bin\everabot_chunk_go.exe
