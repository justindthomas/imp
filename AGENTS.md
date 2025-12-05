# Repository Guidelines

## Project Structure & Module Organization
```
.
├─ config/                # Configuration templates and static files
│   ├─ etc/               # System‑level config (apt, frr, systemd, vpp)
│   ├─ templates/        # Jinja2 templates used by the build scripts
│   └─ usr/               # Files installed into the target image (e.g. bin/)
├─ diagrams/              # Architecture diagrams (PDF/PNG)
├─ scripts/               # Helper scripts for building images, ISO, router etc.
├─ INSTALL.md             # Top‑level install/usage instructions
└─ CLAUDE.md              # Project description
```
All source code lives in the `scripts/` directory; configuration lives under `config/`.  Assets such as diagrams are in `diagrams/`.

## Build, Test, and Development Commands
| Command | Description |
|---|---|
| `./scripts/bootstrap-livecd.sh` | Set up a live‑CD environment for development. |
| `./scripts/build-image.sh` | Build a runnable VM image using the current config. |
| `./scripts/build-installer-iso.sh` | Create an installer ISO from the built image. |
| `./scripts/deploy-image.sh` | Deploy the generated image to a target host. |
| `./scripts/setup-build-vm.sh` | Spin up a VM for iterative builds. |
| `./scripts/setup-router.sh` | Configure a router VM with the generated VPP/Frr configs. |

These scripts are self‑contained; run them from the repository root.  Most rely on Docker/Podman and a recent version of Bash.

## Coding Style & Naming Conventions
* **Shell scripts** – 2‑space indentation, `snake_case` for functions/variables.
* **Jinja2 templates** – Use lower‑case, hyphen‑separated filenames (`*.j2`).
* **Configuration files** – Keep keys lowercase, use underscores where appropriate.
* Run `shellcheck` locally (`shellcheck *.sh`) to catch style issues.

## Testing Guidelines
The repository currently does **not** include an automated test suite.  When adding tests:
* Prefer **BATS** (`bash automated testing system`) for shell script validation.
* Name test files `*_test.bats` and place them in a `tests/` folder.
* Aim for >80 % coverage of critical helper scripts before merging.

## Commit & Pull Request Guidelines
* **Commit messages** – Follow the Conventional Commits format:
  * `feat:` for new features
  * `fix:` for bug fixes
  * `docs:` for documentation updates
  * `chore:` for routine tasks (e.g., script refactor)
  * Include a short imperative summary on the first line and a blank line before an optional body.
* **Pull requests** – Include a concise description, reference the related issue (`Closes #123`), and list any new or updated scripts.
* Ensure the CI (if added later) passes and that `shellcheck` returns no warnings.

## Security & Configuration Tips (Optional)
* Store secrets (e.g., signing keys) **outside** the repo; reference them via environment variables.
* Verify template rendering with `jinja2-cli` before committing changes.
* Review generated systemd service files for correct permissions.

---
These guidelines are meant to help contributors get up to speed quickly while keeping the repository tidy and reproducible.
