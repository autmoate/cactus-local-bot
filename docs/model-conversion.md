# Model Conversion

## Cactus Setup
Upstream Cactus setup is sourced, not executed directly:
```bash
git clone https://github.com/cactus-compute/cactus vendor/cactus
cd vendor/cactus
source ./setup
```

Linux packages listed upstream:
```bash
sudo apt-get install python3.12 python3.12-venv python3-pip cmake build-essential libcurl4-openssl-dev
```

## Gemma-4 E2B
Try download first:
```bash
cactus download google/gemma-4-E2B-it
```

If a local conversion is required:
```bash
cactus convert google/gemma-4-E2B-it ./models/gemma-4-e2b --bits 4
```

Focused local run:
```bash
cactus run ./models/gemma-4-e2b --prompt "Hallo, antworte kurz."
```

Serve for this prototype:
```bash
cactus serve ./models/gemma-4-e2b --host 127.0.0.1 --port 8080 --no-cloud-handoff
```

In this workspace the downloaded CQ4 bundle is:
```text
vendor/cactus/weights/gemma-4-e2b-it-cq4
```

Then set `.env`:
```text
CACTUS_BASE_URL=http://127.0.0.1:8080/v1
CACTUS_MODEL=./models/gemma-4-e2b
```

For the downloaded bundle above, use:
```text
CACTUS_MODEL=vendor/cactus/weights/gemma-4-e2b-it-cq4
```

## Expensive Tests
List upstream test suites before running model-heavy tests:
```bash
cactus test --component engine --list
```
