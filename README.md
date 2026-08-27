# ClickUp Task Exporter

A local Streamlit app that reads ClickUp tasks, shows task analytics, and
exports the current list as CSV or Excel. The app does not store ClickUp
tasks or the Personal API Token.

## Quick start

You need Python 3.11 or newer.

### macOS or Linux

1. Open a terminal in this folder.
2. Run:

   ```bash
   chmod +x run_local.sh
   ./run_local.sh
   ```

3. Open http://localhost:8501 in your browser.

### Windows

1. Open this folder in File Explorer.
2. Double-click `run_local.bat`.
3. Open http://localhost:8501 in your browser.

The first run creates a private `.venv` folder and installs the required
packages. Later runs reuse it.

### Manual setup

```bash
python3 -m venv .venv
source .venv/bin/activate       # macOS/Linux
# Windows PowerShell:
# .\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
streamlit run app.py --server.address 127.0.0.1 --server.port 8501
```

## Using the app

Enter your ClickUp Personal API Token in the sidebar. The app stays idle until
you enter one, then loads your workspaces, spaces, lists, and tasks directly
from ClickUp.

To create a token in ClickUp:

1. Sign in to ClickUp.
2. Open your avatar menu and choose **Settings**.
3. Open **Apps**.
4. Generate or copy your **API Token**.

## Team-only access

For a shared team computer or a controlled internal deployment, create a local
`.env` file from `.env.example` and set `TEAM_ACCESS_CODE` before starting the
app. The current launcher does not automatically read `.env`; set the variable
in the terminal first:

### macOS/Linux

```bash
export TEAM_ACCESS_CODE="your-team-passcode"
./run_local.sh
```

### Windows Command Prompt

```bat
set TEAM_ACCESS_CODE=your-team-passcode
run_local.bat
```

If `TEAM_ACCESS_CODE` is not set, the app has no passcode gate. For a truly
private local setup, keep the app bound to `127.0.0.1` as shown above and set a
team passcode if other people can access the computer.

## Optional local AI analysis

The task exporter works without AI. Replit supplies the AI connection when the
app runs there. To enable AI on your own computer, set `OPENAI_API_KEY` in your
terminal before launching:

```bash
export OPENAI_API_KEY="your-key"
./run_local.sh
```

On Windows Command Prompt:

```bat
set OPENAI_API_KEY=your-key
run_local.bat
```

Never put a real key in `.env.example`, commit it to Git, or paste it into the
app source. The ClickUp token is still entered only in the sidebar.

## Publish with GitHub and Streamlit Community Cloud

GitHub stores the source code. Streamlit Community Cloud runs the Python app.
GitHub Pages alone cannot run this Streamlit application.

1. Create a new GitHub repository. For a team tool, use a **Private** repository.
2. Upload the contents of this `clickup-exporter` folder to the repository.
   The repository root should contain `app.py` and `requirements.txt`.
3. Open [Streamlit Community Cloud](https://share.streamlit.io/) and sign in
   with GitHub.
4. Choose **New app**, select your repository and `main` branch, and set the
   main file to `app.py`.
5. In the app's settings, open **Secrets** and add:

   ```toml
   TEAM_ACCESS_CODE = "your-team-passcode"
   ```

6. If you want AI analysis outside Replit, also add:

   ```toml
   OPENAI_API_KEY = "your-key"
   ```

7. Deploy the app and share the generated URL with your team.

Never upload `.env`, `secrets.toml`, or any ClickUp/API key to GitHub. The
included ignore rules help prevent accidental uploads. A public repository
means anyone can read the source code; a private repository is recommended for
an internal team tool.

## Security notes

- Keep the app on `127.0.0.1`; do not change it to `0.0.0.0` unless you
  deliberately want to share it over a trusted network.
- Treat the ClickUp token as a password. Do not include it in screenshots,
  source files, `.env` files, or exported files.
- Use ClickUp tokens belonging to the appropriate team members and revoke
  tokens that are no longer needed.
- CSV and Excel downloads are created in your browser session; the app does
  not upload them anywhere.