# ornith-launcher

Unofficial tool for running Ornith models locally with llama.cpp. It is not affiliated with Ornith AI or any
other company named here; see [NOTICE](NOTICE) and [DISCLAIMER.md](DISCLAIMER.md).

> Early development. The launcher does not set up or run models yet.

ornith-launcher checks your machine's memory and GPU, then suggests an Ornith model (any version and size), a
quantization and a context size that fit. It downloads the model from Hugging Face (or uses one you already have)
and starts it with llama.cpp. It can also connect the model to Claude Code and OpenCode.

## Requirements

- Python 3.9 or newer. Nothing else to install from PyPI.
- Linux: Docker Engine (with the NVIDIA Container Toolkit for NVIDIA GPUs).

## Quick start

```
git clone https://github.com/ShourieG/ornith-launcher.git
cd ornith-launcher
./ornith-launcher --version
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Security issues: see [SECURITY.md](SECURITY.md).

## Licence

Apache-2.0, see [LICENSE](LICENSE). Models are not part of this project and keep their own licences.
