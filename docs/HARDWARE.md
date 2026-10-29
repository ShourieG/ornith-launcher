# Hardware and platforms

What has been run, and what is only expected to work. Untested platforms stay labelled untested until someone
runs them; reports are welcome.

## Status

| Platform | Runtime | Status |
|---|---|---|
| NVIDIA DGX Spark (GB10, 128 GB unified memory, arm64), Ubuntu | Docker, llama.cpp `server-cuda13` | Tested with Ornith 1.5 35B-A3B (see [BENCHMARKS.md](BENCHMARKS.md)) |
| Linux, NVIDIA discrete GPU, driver 580 or newer | Docker, `server-cuda13` | Untested |
| Linux, NVIDIA discrete GPU, older driver | Docker, `server-cuda` (CUDA 12) | Untested |
| Linux, AMD GPU with ROCm (amd64) | Docker, `server-rocm` | Untested |
| Linux, AMD APU (unified memory) or other GPUs | Docker, `server-vulkan` | Untested |
| Linux, Intel GPU (amd64) | Docker, `server-intel` | Untested |
| Linux, no GPU | Docker, `server` (CPU) | Untested |
| Windows | Native llama.cpp, no Docker | Planned for a later release |
| macOS 13.3 or newer, Apple Silicon | Native llama.cpp b11552 with Metal, no Docker | Tested on a MacBook Pro (M1 Max, 32 GB, macOS 26.6.2) |
| macOS 13.3 or newer, Intel | Native llama.cpp b11552 on the CPU, no Docker | Built, not yet run on an Intel Mac |

## What was run

| Platform | Machine | What was run |
|---|---|---|
| Linux | NVIDIA DGX Spark (GB10, 128 GB unified memory, arm64), Ubuntu 24.04, Ornith 1.5 35B-A3B | Setup with a real download and resume, launching from the desktop icon, the chat log, Claude Code and OpenCode through it, `setup --shortcuts`, every uninstall choice, and Ctrl+C in `bench` |
| macOS | MacBook Pro, Apple M1 Max, 32 GB, macOS 26.6.2, community Ornith 1.5 9B quants | Setup with real downloads, the llama.cpp runtime download, the launch icon app opening iTerm2 windows, installing OpenCode, uninstall, and the Metal memory limit the launcher reads |

Not run yet: other Linux distributions, discrete NVIDIA, AMD and Intel GPUs, CPU-only machines, Intel Macs,
`bench` on a Mac, and every other model and size. The unit tests run on Linux and macOS in CI.

## Linux distributions

The launcher calls no package manager and has little distribution-specific code, so any distribution with
Python 3.9+ and Docker Engine should work. Only one has been run.

| Distribution | Status |
|---|---|
| Ubuntu 24.04 (DGX OS on the DGX Spark, arm64, GNOME) | Tested |
| Other Ubuntu and Debian releases with Python 3.9+ (Ubuntu 22.04+, Debian 11+) | Expected to work, untested |
| Fedora, RHEL 9 and its rebuilds (Rocky, Alma) | Expected to work, untested. SELinux is detected and the container runs with `label=disable` |
| Arch, openSUSE and others | Expected to work, untested |
| Ubuntu 20.04, RHEL 8 | `python3` is too old (3.8 and 3.6). Works once a newer Python is installed alongside (`python3.9`), untested |
| WSL2, Podman, rootless Docker, immutable distributions (Fedora Silverblue) | Untested |

- Docker installed as an Ubuntu snap can only read files under your home folder; the launcher warns about it.
- Desktop icons follow the freedesktop standard. On GNOME the launcher marks its own icon as trusted, so it starts
  with one click; other desktops may ask "Allow Launching" once. Without a desktop (a server, or over SSH) there
  is no icon, and the server runs in the current terminal.
- Terminals the launcher can open for the server window: GNOME Terminal, GNOME Console, Ptyxis, Konsole, kitty,
  Alacritty, WezTerm, foot, Xfce Terminal, Tilix, Terminator, MATE Terminal, LXTerminal and xterm.

## Models

Only the Ornith 1.5 35B-A3B has been run. Plans for every other model and size are ESTIMATED from the GGUF
headers and the memory model, and the 397B models are also marked UNTESTED LOCALLY.

## How memory is planned

The launcher sees memory as pools: one pool for unified memory (DGX Spark, AMD APUs) or for a machine without a
GPU, and a VRAM pool per discrete GPU next to a system RAM pool. A plan must leave 15% of each pool free after the
memory the system already uses. On a discrete GPU, Mixture-of-Experts models (35B, 122B, 397B) can keep their
experts in system RAM (`--cpu-moe`); that placement is ESTIMATED.

On a Mac the GPU can use only part of the memory, a limit Metal sets. The plan uses the whole memory less what the
system uses, capped at that limit. The launcher reads the limit from Metal itself; if that fails, from llama.cpp
once its build is installed; and only then estimates it (two thirds of the memory up to 36 GiB, three quarters
above, ESTIMATED). On the tested M1 Max with 32 GB, Metal reported 24.96 GiB, 78% of the memory (TESTED reading).

## Requirements

- Python 3.9 or newer.
- Linux: Docker Engine, with the NVIDIA Container Toolkit for NVIDIA GPUs. `ornith-launcher doctor` checks both.
- macOS: version 13.3 or newer. Apple Silicon runs the model on the GPU through Metal; an Intel Mac runs it on the
  CPU only, so only small models are practical there. No Docker.
- Disk space for the model (from about 5 GiB for a 9B at Q4_K_M to hundreds of GiB for a 397B) plus 2 GiB, and
  the llama.cpp server: a few GB for the Docker image on Linux, about 12 MB on macOS.
- Memory for the model and its context: from about 10.5 GiB for a 9B at Q4_K_M with a 32k context and vision off
  (ESTIMATED). On a small machine, open All models in setup and sort it by "memory needed, smallest first", or
  sort the best fit list by "best for small memory".
