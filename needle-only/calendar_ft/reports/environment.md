# Environment Report — Calendar-FT auf Jetson AGX Orin

Datum: 2026-09-10 · Repo: `autmoate/cactus-local-bot` @ `ecd01ce` (v6 World Model)

## Hardware / OS

| Komponente | Wert |
|---|---|
| Board | NVIDIA Jetson AGX Orin 64 GB |
| L4T / JetPack | R36 (release), Revision 4.7 (JetPack 6.x) |
| Kernel | 5.15.148-tegra, aarch64 |
| RAM | 61 GiB (unified memory) |
| GPU | integrierter Orin GPU, Compute Capability **8.7** (sm_87) |
| Treiber | 540.4.0 |

## Software-Stack

| Komponente | Version |
|---|---|
| Python | 3.11.14 (uv-managed venv) |
| cactus-needle | 2.0.13 (`[train,gpu]` Extra) |
| jax / jaxlib | 0.10.2 (CUDA 12-Wheel, aarch64) |
| CUDA (System) | 12.6 (nvcc: `cuda_12.6.r12.6`) |
| CUDA (pip-Wheels) | 12.9-Familie (`nvidia-cublas-cu12==12.9.2.10` etc.) |
| cuDNN (System) | 9.3.0.75 (`libcudnn9-cuda-12`) |
| JAX-Backend | **gpu** — `jax.devices()` → `[CudaDevice(id=0)]` |

## JAX-GPU-Verifikation (Pflicht vor jedem Training)

```bash
source .venv/bin/activate
unset LD_LIBRARY_PATH
XLA_PYTHON_CLIENT_PREALLOCATE=false XLA_FLAGS="--xla_gpu_autotune_level=0" \
python -c "import jax; print(jax.devices())"
# -> [CudaDevice(id=0)]
```

Zusätzlich Matmul-Smoke-Test (2048×2048, JIT): `0.37 s` auf `cuda:0`.

## Jetson-spezifische Workarounds (reproduzierbar)

1. **`LD_LIBRARY_PATH` leeren.** Der Orin hat System-CUDA 12.6 in
   `/usr/local/cuda` und `LD_LIBRARY_PATH` enthält `/usr/local/cuda-12.6/lib64`
   (teils 7×). Der Loader findet darüber die System-CUDA-12.6-Libs *vor* den
   pip-installierten CUDA-12.9-Wheels. `pip cusparse 12.5` braucht aber
   `__nvJitLinkGetErrorLogSize_12_9` — Symbol-Auflösung schlägt fehl:
   `Unable to load cuSPARSE. Is it installed?`
   Ohne `LD_LIBRARY_PATH` greifen die RUNPATHs der pip-Wheels
   (`$ORIGIN/../../nvjitlink/lib` …) und die pip-Familie ist konsistent.

2. **`XLA_FLAGS="--xla_gpu_autotune_level=0"`.** Der XLA-GPU-Autotuner crasht
   auf dem Orin (sm_87) reproduzierbar:
   `F ... stream_executor_util.cc:519] Could not load RepeatBufferKernel: …
   cudaErrorNoKernelImageForDevice`.
   Vor dem Crash versucht XLA ~46 GiB unified memory zu reservieren
   (Default-Preallocation = 75 % von 64 GB), was auf dem Orin mit ~42 GiB
   freiem RAM in `CUDA_ERROR_OUT_OF_MEMORY` endet. Mit deaktiviertem
   Autotuning wird dieser Pfad komplett übersprungen.

3. **`XLA_PYTHON_CLIENT_PREALLOCATE=false`.** Verhindert die 46-GiB-
   Preallocation. Auf dem unified-memory Orin wollen GPU-Training und Rest-
   System denselben RAM teilen — ohne dieses Flag kann der erste große
   `device_put` OOM gehen.

Die Kombination (1)+(2)+(3) ist stabil getestet: Matmul 2048² in 0.37 s auf
`cuda:0`, LoRA-Finetuning läuft auf GPU.

## Needle-Engine

| Komponente | Wert |
|---|---|
| Engine-Generation | 2 (`libneedle2.so`, Cache: `~/.cache/cactus-needle/v2/`) |
| Base-Checkpoint | `checkpoints/needle2.pkl` (HF: `Cactus-Compute/needle2`) |
| LoRA-Targets | `q_proj, k_proj, v_proj, gate_proj, out_proj` (alle Layer) |
| Export | `.cact` (W4A8, QAT-aware; `needle build`) |

## Reproduktion

```bash
# 1. uv-Umgebung (Repo-Root)
uv venv --python 3.11
source .venv/bin/activate
uv pip install "cactus-needle[train,gpu]"

# 2. JAX-GPU-Check (siehe oben) MUSS cuda:0 zeigen

# 3. Kompletter Flow pro Task
python needle-only/calendar_ft/build_dataset.py --task calendar_write \
    --count 2000 --seed 42 --out needle-only/calendar_ft/data/train/calendar_write.jsonl
python needle-only/calendar_ft/train.py --task calendar_write \
    --epochs 10 --lora-rank 16 --lora-alpha 32
python needle-only/calendar_ft/eval_model.py --task calendar_write \
    --dataset needle-only/calendar_ft/data/eval/calendar_write.jsonl \
    --weights needle-only/calendar_ft/models/calendar_write.cact \
    --out needle-only/calendar_ft/reports/calendar_write_ft.json
```
