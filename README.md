# PHANES — AI Operating Layer for Windows

PHANES is a Python-based AI operating layer designed to turn natural-language requests into development workflows across your local workspace. It brings together task planning, filesystem operations, terminal and Git tooling, browser research, and persistent task history.

## Features

- Natural-language task requests through a desktop UI built with PySide6.
- LLM-powered edit proposals using Google Gemini, with OpenRouter fallback support.
- Human approval before protected file modifications and terminal operations.
- Workspace-confined filesystem tools.
- Git status, branch, and commit inspection.
- Persistent task state and history using SQLite.
- Tests for planning, execution, approval, filesystem safety, and LLM integration.

PHANES is an active prototype. Review proposed changes and grant approval only when you understand the action being requested.

## Requirements

- Windows 10 or Windows 11
- Python 3.11 recommended
- Git
- API credentials for the LLM providers you intend to use

## Setup

Clone the repository and enter the project directory:

```powershell
git clone https://github.com/manishbeth18/Phanes-AI-OS.git
cd Phanes-AI-OS
```

Create and activate a virtual environment:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Install dependencies:

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## Configure API keys

Create a local `.env` file from the example:

```powershell
Copy-Item .env.example .env
```

Add the credentials for the providers you want to use:

```dotenv
GEMINI_API_KEY=your_gemini_api_key
OPENROUTER_API_KEY=your_openrouter_api_key
```

Keep `.env` private. Never commit real API keys.

## Run PHANES

From the repository root, with the virtual environment activated:

```powershell
python -m ui.main_window
```

Select a workspace and submit a task. For protected operations, inspect the proposed action and approve it only if it is correct.

## Run tests

```powershell
python -m pytest -q
```

## 📸 PHANES UI Preview

Here's a preview of the PHANES AI-OS desktop interface.
<img width="1600" height="899" alt="PIC 1" src="https://github.com/user-attachments/assets/96369bde-e552-4d09-9f4e-0177a68e2857" />






## Safety notes

- Protected operations require approval through the execution system.
- Filesystem tools are designed to remain within the selected workspace.
- An LLM-generated proposal is not proof that a change is correct.
- Completed tool calls may still be marked unverified. Test important changes independently.
- PHANES is an early-stage project. Do not rely on it to make unsupervised changes to important files or repositories.

## Contributing

Contributions and bug reports are welcome. Run the test suite before submitting changes, and include tests for new planner, executor, or safety behavior.

## License

A license has not yet been specified. Until a license is added, do not assume that the repository grants permission to redistribute or reuse its code.
