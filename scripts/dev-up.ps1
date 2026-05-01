param(
    [ValidateSet("fast", "service", "comfyui", "all")]
    [string]$Mode = "fast"
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

switch ($Mode) {
    "fast" {
        Write-Host "[dev-up] Fast path: start service without rebuild/deps..."
        docker compose up -d --no-deps image-gen-service
    }
    "service" {
        Write-Host "[dev-up] Rebuild service only (no deps)..."
        docker compose up -d --no-deps --build image-gen-service
    }
    "comfyui" {
        Write-Host "[dev-up] Rebuild comfyui image only..."
        docker compose build comfyui
    }
    "all" {
        Write-Host "[dev-up] Rebuild comfyui, then rebuild/start service..."
        docker compose build comfyui
        docker compose up -d --no-deps --build image-gen-service
    }
}
