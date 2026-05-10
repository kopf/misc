# Project Instructions: Once-off Scripts Repository

This repository contains a collection of self-contained, once-off Python scripts. Each script is organized into its own directory following specific naming and implementation conventions.

## Repository Structure

- **Directories:** Every new script must live in its own directory named as `YYYYMMDD - descriptive-name`.
  - `YYYYMMDD` is the creation date (e.g., `20260510`).
  - `descriptive-name` is a short, slug-like description (e.g., `openwrt-slowdown-diagnosis`).
- **Isolation:** Scripts must be self-contained within their directory. Do not depend on shared modules across directories.

## Script Implementation (Python)

- **Dependency Management:** Use `uv` hashbangs. DO NOT create `requirements.txt`, `pyproject.toml`, or virtual environments.
  - Requirements must be defined in the script header using the inline metadata format.
  - Example:
    ```python
    #!/usr/bin/env uv run
    # /// script
    # requires-python = ">=3.12"
    # dependencies = [
    #     "pandas",
    #     "matplotlib",
    # ]
    # ///

    import pandas as pd
    import matplotlib.pyplot as plt
    # ... rest of the script
    ```
- **Modern Style:** Use modern Python features (Type hints, `pathlib`, etc.).
- **Executable:** Ensure scripts are made executable (`chmod +x`).

## Git & File Management

- **Ignore Generated Files:** If a script generates output files (plots, logs, CSVs), create a `.gitignore` file within that script's directory to ignore them.
  - Example `.gitignore` content: `*.png`, `*.log`, `output/`.
- **No Staging:** Do not stage or commit changes unless explicitly requested.

## Workflow for New Scripts

1. **Research/Strategy:** Understand the goal and required libraries. Ask questions to clarify any unclear points.
2. **Directory Creation:** Create `YYYYMMDD - name/`.
3. **Implementation:** Write `main.py` (or a descriptive name) with the `uv` hashbang and required dependencies.
4. **Git Ignore:** Add `.gitignore` for any anticipated output files.
