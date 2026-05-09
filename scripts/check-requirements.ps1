#Requires -Version 5.0

<#
.SYNOPSIS
    Verifica los requisitos previos para correr SecondBrain en Windows.

.DESCRIPTION
    Chequea que Docker, NVIDIA GPU, WSL2 y otras dependencias estén OK.
    NO instala nada, sólo reporta el estado.

.EXAMPLE
    .\check-requirements.ps1
#>

$ErrorActionPreference = "Continue"

function Write-Result {
    param(
        [string]$Name,
        [bool]$Ok,
        [string]$Detail = ""
    )
    $icon = if ($Ok) { "✅" } else { "❌" }
    $line = "$icon $Name"
    if ($Detail) { $line += " — $Detail" }
    Write-Host $line
}

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  SecondBrain — Verificación de requisitos" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

$allOk = $true

# -----------------------------------------------------------
# WSL2
# -----------------------------------------------------------
Write-Host "[Sistema operativo]" -ForegroundColor Yellow
try {
    $wslVersion = wsl --version 2>&1 | Out-String
    if ($wslVersion -match "WSL version") {
        Write-Result "WSL2 instalado" $true ($wslVersion -split "`n" | Select-Object -First 1)
    } else {
        Write-Result "WSL2 instalado" $false "ejecutar: wsl --install"
        $allOk = $false
    }
} catch {
    Write-Result "WSL2 instalado" $false "no disponible"
    $allOk = $false
}

# -----------------------------------------------------------
# Docker
# -----------------------------------------------------------
Write-Host ""
Write-Host "[Docker]" -ForegroundColor Yellow
try {
    $dockerVersion = docker version --format json 2>$null | ConvertFrom-Json
    if ($dockerVersion) {
        Write-Result "Docker instalado" $true "v$($dockerVersion.Client.Version)"
        Write-Result "Docker daemon corriendo" $true "v$($dockerVersion.Server.Version)"
    } else {
        Write-Result "Docker daemon corriendo" $false "iniciar Docker Desktop"
        $allOk = $false
    }
} catch {
    Write-Result "Docker instalado" $false "instalar Docker Desktop"
    $allOk = $false
}

try {
    $composeVersion = docker compose version --short 2>$null
    if ($composeVersion) {
        Write-Result "Docker Compose v2" $true "v$composeVersion"
    } else {
        Write-Result "Docker Compose v2" $false "actualizar Docker Desktop"
        $allOk = $false
    }
} catch {
    Write-Result "Docker Compose v2" $false ""
    $allOk = $false
}

# -----------------------------------------------------------
# GPU NVIDIA
# -----------------------------------------------------------
Write-Host ""
Write-Host "[GPU NVIDIA]" -ForegroundColor Yellow
try {
    $gpuInfo = nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader 2>$null
    if ($gpuInfo) {
        $parts = $gpuInfo -split ","
        Write-Result "GPU NVIDIA detectada" $true ($parts -join " · ")
    } else {
        Write-Result "GPU NVIDIA detectada" $false "instalar drivers NVIDIA"
        $allOk = $false
    }
} catch {
    Write-Result "GPU NVIDIA detectada" $false "nvidia-smi no encontrado"
    $allOk = $false
}

# Test GPU desde Docker
try {
    Write-Host "Probando GPU desde Docker..." -ForegroundColor Gray
    $result = docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi 2>&1 | Out-String
    if ($result -match "NVIDIA-SMI") {
        Write-Result "GPU accesible desde Docker" $true ""
    } else {
        Write-Result "GPU accesible desde Docker" $false "verificar nvidia-container-toolkit"
        $allOk = $false
    }
} catch {
    Write-Result "GPU accesible desde Docker" $false "test falló: $($_.Exception.Message)"
    $allOk = $false
}

# -----------------------------------------------------------
# Recursos disponibles
# -----------------------------------------------------------
Write-Host ""
Write-Host "[Recursos]" -ForegroundColor Yellow
try {
    $os = Get-CimInstance Win32_OperatingSystem
    $totalRAM_GB = [math]::Round($os.TotalVisibleMemorySize / 1024 / 1024, 1)
    $freeRAM_GB = [math]::Round($os.FreePhysicalMemory / 1024 / 1024, 1)
    $ramOk = $totalRAM_GB -ge 16
    Write-Result "RAM total" $ramOk "$totalRAM_GB GB (libre: $freeRAM_GB GB) — recomendado >=32 GB"

    $disk = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'"
    $freeDisk_GB = [math]::Round($disk.FreeSpace / 1024 / 1024 / 1024, 1)
    $diskOk = $freeDisk_GB -ge 100
    Write-Result "Disco libre (C:)" $diskOk "$freeDisk_GB GB — recomendado >=200 GB"
} catch {
    Write-Host "  No se pudieron obtener recursos del sistema" -ForegroundColor Gray
}

# -----------------------------------------------------------
# Git
# -----------------------------------------------------------
Write-Host ""
Write-Host "[Git]" -ForegroundColor Yellow
try {
    $gitVersion = git --version 2>$null
    if ($gitVersion) {
        Write-Result "Git instalado" $true $gitVersion
    } else {
        Write-Result "Git instalado" $false "instalar con: winget install Git.Git"
        $allOk = $false
    }
} catch {
    Write-Result "Git instalado" $false ""
    $allOk = $false
}

# -----------------------------------------------------------
# Resumen
# -----------------------------------------------------------
Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
if ($allOk) {
    Write-Host "✅ Todos los requisitos están OK" -ForegroundColor Green
    Write-Host ""
    Write-Host "Próximos pasos:" -ForegroundColor White
    Write-Host "  1. cp .env.example .env" -ForegroundColor Gray
    Write-Host "  2. Editar .env con tus credenciales" -ForegroundColor Gray
    Write-Host "  3. docker compose up -d" -ForegroundColor Gray
    Write-Host "  4. Esperar descarga de modelos (20-30 min primera vez)" -ForegroundColor Gray
    Write-Host "  5. http://localhost:8501" -ForegroundColor Gray
} else {
    Write-Host "⚠️  Hay requisitos faltantes" -ForegroundColor Yellow
    Write-Host "Revisar los items con ❌ y resolverlos antes de continuar" -ForegroundColor Yellow
}
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""
