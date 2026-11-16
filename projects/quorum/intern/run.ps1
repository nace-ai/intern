# Runs Intern in Docker against this repo.
# Keys are read from .intern-home\.env (NACE_API_KEY, DEEPINFRA_API_KEY). That folder is git-ignored.
# Usage:
#   .\intern\run.ps1 "task text"                 # headless, readable output
#   .\intern\run.ps1 -Json "task text"           # one JSON event per line (used by the backend)
#   .\intern\run.ps1 -Interactive                # terminal UI
param(
    [Parameter(Position = 0)][string]$Task,
    [string]$Model = $(if ($env:INTERN_SYSTEM2) { $env:INTERN_SYSTEM2 } else { "deepinfra/anthropic/claude-opus-5-5" }),
    [switch]$Json,
    [switch]$Interactive,
    [int]$MaxTurns = 30,
    # Folder (under the repo root) that Intern uses for its memory and sessions.
    # Coding runs use .intern-home-coder so they never share memory with the arena's
    # note-writing agent. Keys are always read from .intern-home\.env.
    [string]$InternHome = ".intern-home"
)
$root = Split-Path -Parent $PSScriptRoot
New-Item -ItemType Directory -Force "${root}\$InternHome" | Out-Null
$common = @(
    "--rm",
    "-v", "${root}:/work",
    "-v", "${root}\${InternHome}:/intern-home",
    "--env-file", "${root}\.intern-home\.env",
    "-e", "OLLAMA_BASE_URL=http://host.docker.internal:11434/v1"
)
if ($Interactive) {
    docker run -it @common quorum-intern --system2 $Model
} elseif ($Json) {
    docker run @common quorum-intern -p --json --max-turns $MaxTurns --system2 $Model -- $Task
} else {
    docker run @common quorum-intern -p --max-turns $MaxTurns --system2 $Model $Task
}
