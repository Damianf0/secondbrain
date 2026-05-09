# Setup en Windows 11

Guía paso a paso para levantar SecondBrain en tu equipo de desarrollo.

## Requisitos previos

### Hardware
- CPU: Intel i7 10ma generación o superior
- RAM: 32 GB (mínimo)
- GPU: NVIDIA RTX 3080 8GB VRAM o superior con CUDA
- Storage: 200 GB libres en SSD NVMe
- OS: Windows 11

### Software base
1. **Windows 11 con WSL2 habilitado**
   ```powershell
   # En PowerShell como Admin
   wsl --install
   wsl --set-default-version 2
   ```

2. **NVIDIA Drivers (latest)**
   - Descargar de https://www.nvidia.com/Download/index.aspx
   - Verificar:
   ```powershell
   nvidia-smi
   ```

3. **Docker Desktop con backend WSL2**
   - Descargar de https://www.docker.com/products/docker-desktop
   - En Settings → General: activar "Use WSL 2 based engine"
   - En Settings → Resources → WSL Integration: habilitar tu distro
   - En Settings → Docker Engine: agregar configuración GPU si no está

4. **NVIDIA Container Toolkit**
   - Verificar acceso a GPU desde Docker:
   ```bash
   docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi
   ```
   Si funciona, ves la tabla de la GPU. Si no, instalar el toolkit (Docker Desktop suele ya traerlo).

5. **Git para Windows**
   ```powershell
   winget install Git.Git
   ```

## Setup del proyecto

### 1. Clonar el repo

```powershell
cd C:\
mkdir secondbrain
cd secondbrain
git clone <url-de-tu-repo> .
```

### 2. Configurar variables de entorno

```powershell
copy .env.example .env
notepad .env
```

**Cambiar obligatoriamente**:
- `POSTGRES_PASSWORD` — password de Postgres
- `MINIO_ROOT_PASSWORD` — password de MinIO (mínimo 8 chars)
- `QDRANT_API_KEY` — string aleatoria larga
- `BACKEND_SECRET_KEY` — string aleatoria de 32+ chars

Generar valores seguros:
```powershell
# En PowerShell
[System.Web.Security.Membership]::GeneratePassword(32, 5)
```

O usar `openssl` desde WSL:
```bash
openssl rand -hex 32
```

### 3. Levantar los servicios

Primer arranque (descarga imágenes y modelos, **20-30 minutos**):

```powershell
docker compose up -d
```

Ver el progreso:

```powershell
docker compose logs -f
```

Particularmente importante mirar:

```powershell
# Descarga de modelos Ollama (primer arranque)
docker compose logs -f ollama-init

# Descarga de modelo Whisper (primer arranque)
docker compose logs -f whisper
```

### 4. Verificar que todo está OK

```powershell
docker compose ps
```

Todos los servicios deben estar en `healthy` o `running`.

### 5. Abrir el panel

Browser → http://localhost:8501

Deberías ver el dashboard con todos los servicios verdes.

## URLs de los servicios

| Servicio | URL | Descripción |
|---|---|---|
| Frontend (Streamlit) | http://localhost:8501 | Panel admin |
| Backend (FastAPI) | http://localhost:8000 | API REST |
| API Docs | http://localhost:8000/docs | Swagger UI |
| Qdrant Dashboard | http://localhost:6333/dashboard | Vector DB UI |
| MinIO Console | http://localhost:9001 | Storage UI (user/pass del .env) |
| Ollama | http://localhost:11434 | LLM server |
| Whisper | http://localhost:9000/docs | Transcription API |

## Operaciones comunes

### Levantar todo
```powershell
docker compose up -d
```

### Bajar todo
```powershell
docker compose down
```

### Bajar y borrar datos (CUIDADO: pierdes todo)
```powershell
docker compose down -v
```

### Ver logs en vivo de un servicio
```powershell
docker compose logs -f backend
docker compose logs -f frontend
docker compose logs -f ollama
```

### Reiniciar un servicio
```powershell
docker compose restart backend
```

### Entrar a un container
```powershell
docker compose exec backend bash
docker compose exec postgres psql -U secondbrain -d secondbrain
```

### Descargar manualmente un modelo Ollama
```powershell
docker compose exec ollama ollama pull qwen3:8b
```

## Troubleshooting

### "Cannot connect to Docker daemon"
- Verificar que Docker Desktop está corriendo
- Reiniciar Docker Desktop

### "GPU not found" en Ollama
- Verificar `nvidia-smi` en PowerShell
- Verificar `docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi`
- Reiniciar Docker Desktop

### Backend no levanta
- `docker compose logs backend`
- Verificar que Postgres está healthy primero
- Verificar variables de entorno (.env)

### Modelo Ollama tarda mucho en cargar la primera vez
- Es normal: tiene que cargarlo a VRAM
- A partir de la segunda vez es casi instantáneo (mientras `OLLAMA_KEEP_ALIVE` no expire)

### "Out of memory" en GPU
- Solo cargar un modelo a la vez (descargar el otro)
- O bajar a quantización Q4 más agresiva

### Whisper falla en arrancar
- Es normal: la primera vez descarga el modelo (~3 GB)
- Esperar 1-2 minutos
- Verificar `docker compose logs whisper`

## Próximos pasos

Una vez que tengas Sprint 0 funcionando:

1. **Probar el LLM**: ir a Benchmark, hacer un prompt, ver la respuesta
2. **Probar embeddings**: en Benchmark, tab Embeddings
3. **Comparar modelos**: en Benchmark, tab Comparar — Gemma 4 vs Qwen3-VL
4. **Subir un archivo**: en Vault, subir una imagen o audio
5. **Reportar resultados**: ¿qué velocidad tiene tu hardware?

Después, avanzamos a Sprint 1 (importación de WhatsApp).
