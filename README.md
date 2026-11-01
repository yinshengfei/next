# Next

A small, local, **read-only** assistant for choosing what to work on now.

Choose your available time and energy. Next reads Todoist, shortlists at most 18 tasks locally, and asks AI for one next action and two alternatives. **Make It Easier** turns a recommendation into 1–3 smaller steps.

There is no task completion, task editing, progress tracking, calendar integration, database, login flow, public deployment, or background AI activity in this version.

## Run on your Mac

**Applications / Dock:** Open `/Applications/Next.app`. It starts the local app in the background and opens your default browser when ready. While Next stays open, it recovers the service if a previously reused Terminal server exits. Quit **Next** from the Dock to stop a server managed by the launcher. See [launcher instructions](macos/README.md) for shutdown, logs, and troubleshooting.

### 1. Open Terminal and enter the project folder

From the directory containing `next`, run the relative-path command below. You can also open Terminal directly in the project folder and skip this command. All later commands assume you are in the project root (the folder containing this README).

```bash
cd ./next
```

If you already have a `.venv` in this project, activate it:

```bash
source .venv/bin/activate
```

If you copy the project to another Mac, or need to recreate the environment, use Python 3.9 or newer:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

The app was tested here with Python 3.9.6. A current Python installation is preferable for ongoing use. No Node.js or npm is needed.

### 2. Get your two credentials

- **Todoist:** open Settings → Integrations → Developer and copy your personal API token. [Todoist developer guide](https://developer.todoist.com/).
- **AI provider:** use an official [OpenAI API key](https://platform.openai.com/api-keys), or follow the OpenAI Next Credits instructions below for your MiraclePlus credits. The account needs access to the configured model and sufficient quota.

Do not paste either credential into this README, source code, chat, or a shell command.

### 3. Save them privately

```bash
python -m next_app setup
```

Enter each credential at the hidden prompt; no characters appear while you type or paste. Press Return after each. macOS may ask you to allow Python access to the login Keychain. Only approve the prompt if you initiated this setup or app request.

Credentials are stored under **Next Local Task Assistant** in your macOS login Keychain. Todoist uses account `todoist`; official OpenAI uses the existing `openai` account. Each custom API base URL has a separate `ai:<base_url>` account. They are never saved in the project or a `.env` file. Re-run setup to replace a credential; leaving a prompt blank keeps the value for that endpoint. Setup saves values but does not make live API requests to verify them.

### 4. Start Next

```bash
python -m next_app
```

Keep Terminal open. In your browser, open:

**http://127.0.0.1:8765**

Select time and energy, then click **Find My Next Action**. Each result includes an estimated duration, a reason, and a concrete first step. Click the original task name to open Todoist, or **Make It Easier** for smaller steps.

**Show other tasks** requests a new shortlist excluding the three currently displayed task IDs. If no other tasks exist, the current recommendations remain visible. Changing time or energy clears the old results so they cannot be mistaken for new recommendations.

### 5. Stop and reopen

Press **Control-C** in the running Terminal to stop the server.

For later use, open Terminal in the project root and run:

```bash
source .venv/bin/activate
python -m next_app
```

Your credentials remain in Keychain. There is no background service or auto-start installation.

## MiraclePlus / OpenAI Next Credits

The Credits [quickstart](https://credits.openai-next.com/guide/quickstart) documents **`https://api.openai-next.com/v1`** as its OpenAI-compatible base URL. Its [Codex guide](https://credits.openai-next.com/guide/cli/codex) explicitly configures `wire_api = "responses"`, matching Next's existing Responses API. The public documentation does **not** establish strict JSON-schema support for every model. Use the check command below to test the exact recommendation request after saving your key.

1. Stop Next with **Control-C** if it is running. Open Terminal in the project root:

   ```bash
   source .venv/bin/activate
   ```

2. Configure the endpoint and model, and enter your key privately:

   ```bash
   python -m next_app setup --base-url 'https://api.openai-next.com/v1' --model 'gpt-6.1-sol'
   ```

   `gpt-6.1-sol` is a currently listed text-model example from the provider's [available models](https://credits.openai-next.com/guide/resources). Use the exact model ID available to your event/account if it differs. The previous default, `gpt-5.4-mini`, is not on that public list, so its availability through this provider is unconfirmed. If your event's **接入信息** lists a different base URL, use that HTTPS URL instead, including `/v1` for the OpenAI-compatible protocol.

   At **Todoist API token**, press **Return** to retain your existing token (or enter it if you have not saved it yet). At **AI provider API key**, paste the key created in the Credits console, then press **Return**. Input is hidden and does not become a shell command or enter shell history. Do not include the key in the `--base-url` argument.

   This saves only public endpoint/model preferences in `~/Library/Application Support/Next/config.json`. The Credits key goes to Keychain account `ai:https://api.openai-next.com/v1`. Your official OpenAI key, if saved earlier, remains in its separate account. A changed base URL requires its own saved key, even when it is another endpoint of the same provider.

3. Verify compatibility before sending your own tasks:

   ```bash
   python -m next_app check
   ```

   This makes **one AI request using a synthetic task**, without reading Todoist, and uses provider credits. It exercises the same model, endpoint, `store=false`, output limit, Responses API, JSON-schema request, and recommendation validation as the app. Success means this request was accepted and its output passed Next's validation; it does not independently prove the gateway enforces every schema constraint upstream.

   If the provider rejects the model, endpoint, or JSON-schema format, the command exits with an error. Check your event's endpoint, exact model ID, access, and credits, or confirm Responses and strict JSON-schema support with the provider. There is no silent retry through official OpenAI or another protocol. A successful recommendations check does not replace testing real recommendation quality and Make It Easier in the app.

4. Start Next:

   ```bash
   python -m next_app
   ```

   Open **http://127.0.0.1:8765** and use the same interface as before. No new packages or frontend build are needed for this provider change.

## How the shortlist works

The backend retrieves the active tasks and project names using all Todoist result pages. It does not send the full task list to AI or store a local task database.

It chooses up to **18 distinct candidates**, using three simple buckets:

1. Up to **6 overdue or next-seven-day tasks**, ordered by their earliest due date or hard deadline, then priority.
2. Up to **6 longer-term or undated tasks**, taking turns across projects. P1/P2 tasks, favorite projects, optional important projects, and `long-term`, `long_term`, or `important` labels are preferred. Ordinary undated tasks can still appear without adding labels.
3. Up to **6 remaining P1/P2 tasks**, taking turns across projects.

Unused slots are filled from remaining tasks, ordered by priority and date, with project variety. A task is included only once. The optional signals use your existing Todoist information; you do not need to maintain another task list.

The AI then considers your time, energy, actual dates, priorities, and long-term progress. Task IDs are checked against the shortlist; duplicate IDs, unknown tasks, missing recommendations, and actions exceeding available time are rejected. Smaller-step estimates must fit the available time in total. Times remain AI estimates, and feasibility and recommendation quality need testing with your real work.

This is deliberately a small shortlist, not exhaustive review of everything you could do. A quiet low-priority project can be missed. Marking an existing Todoist project as a favorite, raising a task's priority, or adding an optional important project below can help without creating new tasks.

## Optional preferences

Setup creates this **non-secret** file:

```text
~/Library/Application Support/Next/config.json
```

Its defaults are:

```json
{
  "provider": "openai",
  "base_url": "https://api.openai.com/v1",
  "model": "gpt-5.4-mini",
  "port": 8765,
  "language": "English",
  "important_projects": []
}
```

Stop the server, edit the file, then restart. For Chinese recommendations, set `"language": "简体中文"`. The interface remains English. `important_projects` can contain exact Todoist project names (case-insensitive) or IDs, such as `["Research", "Career"]`. Defaults work without editing anything.

The existing OpenAI SDK now accepts a configurable HTTPS `base_url` and `model`. The endpoint/model must support the **Responses API with strict JSON-schema output**; Chat Completions compatibility alone is insufficient. Use the API base URL (such as `https://api.openai-next.com/v1`), without `/responses` or `/chat/completions`, embedded credentials, query parameters, or fragments. `provider` is `openai` or `openai-compatible`; requests and Keychain selection use `base_url`. Old configuration files without this field continue to use official OpenAI and the existing key.

Setup accepts `--base-url` and `--model` flags and preserves your other settings. These flags apply only to `setup`; `run` and `check` use the saved preferences. On official OpenAI, GPT-5.4 models retain the previous reasoning effort `none` setting. Third-party endpoints receive no model-specific reasoning override, to avoid assuming they support that parameter; their model defaults may take longer. AI requests use a 60-second timeout and no automatic retries. An upstream AI timeout does not stop Next's local server; the page explains that Next is still running and allows a manual retry.

Next uses the Mac's local date and timezone. A Todoist due date and hard deadline are kept distinct. It never changes either one.

## Privacy and request costs

- **Local does not mean offline.** Todoist receives read requests; the configured AI provider receives selected task data to produce recommendations. With OpenAI Next Credits, requests go to its gateway and are subject to its and the upstream model provider's policies.
- At most 18 candidates are sent, containing task IDs, titles, project names, dates, priorities, limited labels, existing duration if provided, a parent title if available, and descriptions truncated to 700 characters. Truncation is flagged. Comments, attachments, completed tasks, and unrelated account data are not read.
- Make It Easier sends only the selected task snapshot and its recommendation, plus time and energy.
- Each click makes one AI request, with a 2,200-token output limit and no automatic retries. No AI request is made for an empty task list. Exact cost depends on input size, model, and usage.
- `store=false` is sent with every AI request. Official OpenAI documents this as disabling response storage for later retrieval; third-party gateway handling must be confirmed with that provider. This is **not** a promise of zero provider-side retention. See [OpenAI's API documentation](https://developers.openai.com/api/reference/resources/responses/methods/create).
- Only the latest recommendations and task snapshots remain in process memory, becoming unavailable to Make It Easier after 30 minutes or when replaced. They are not saved to disk. Reloading the browser clears its displayed results.
- The server binds only to `127.0.0.1`. Same-origin request checks, a required custom request header, and a restrictive content security policy provide basic browser protection without accounts or authentication infrastructure.
- Model and Todoist text is inserted as text, not HTML. Credentials and upstream error bodies are not included in responses or request logs. Keychain access failure never falls back to plaintext storage.
- Keychain protects normal credential storage; it does not isolate this application from malicious software already controlling your Mac account. A Todoist personal token can have broader permissions than the read operations this app uses.

## Troubleshooting

| Symptom | What to do |
| --- | --- |
| Setup needed | Activate `.venv`, run `python -m next_app setup`, enter both credentials, refresh. |
| Keychain unavailable | Unlock your login keychain; re-run setup in an interactive Terminal. |
| Todoist rejected the token | Copy the current API token and re-run setup. |
| AI provider rejected the key | Re-run setup for the saved base URL and enter that provider's key. |
| Quota or rate limit reached | Check API billing/quota, or wait before retrying. |
| Model or request format unavailable | Check the base URL and exact model ID; run `python -m next_app check`. The endpoint needs Responses API and JSON-schema support. |
| Connection or timeout error | Check internet access; retry after the current request finishes. |
| Suggestion expired | Click Find My Next Action again. |
| Address already in use | Stop another Next instance, or change `port` in the preferences file and restart. |
| Cannot reach Next | Keep its Terminal process running and use the URL it prints. |

To delete saved credentials, open macOS **Keychain Access**, find the relevant entries with the service **Next Local Task Assistant**, and delete those entries yourself. Custom endpoints have separate entries. The configuration file contains no keys.

## Verification

Install the test dependency and run:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Tests use simulated HTTP responses with the actual Todoist client and OpenAI SDK, not your credentials. They cover pagination, candidate bounds and project variety, task identity, time limits, breakdowns, read-only behavior, missing credentials, upstream errors, local request checks, file permissions, custom endpoint/model routing, endpoint-specific credentials, redirect protection, old configuration compatibility, and the synthetic provider check.

The initial browser flow was also checked with fake task and AI responses. The provider change leaves the interface intact. **Your actual Todoist account, Credits key/model access, strict JSON-schema compatibility, live latency, and recommendation quality remain to be verified after you run setup.**

For your first real check, try 15 minutes / Low, then 60 minutes / High. Check whether the chosen tasks are useful, the first steps are specific, and Make It Easier reduces effort. We should refine that experience before adding any write operations or other features.

## Small code map

```text
next_app/
  __main__.py    Terminal setup and launch commands
  config.py      Preferences and macOS Keychain
  todoist.py     Paginated read-only task/project retrieval
  candidates.py  Three-bucket shortlist, maximum 18 tasks
  ai.py          Structured recommendations and smaller steps
  app.py         Local routes and in-memory recommendation snapshots
  models.py      Input/output validation
  static/        Single-page interface, no build step
tests/           Offline integration and behavior tests
```

API references: [Todoist API v1](https://developer.todoist.com/api/v1/), [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs), [default model](https://developers.openai.com/api/docs/models/gpt-5.4-mini).
