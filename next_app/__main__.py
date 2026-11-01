import argparse
import asyncio
import getpass
import sys
import warnings

from pydantic import ValidationError

from .config import CONFIG_PATH, OPENAI_BASE_URL, Settings, SetupError, get_secret, load_settings, save_settings, set_secret
from .todoist import ServiceError


def setup(base_url=None, model=None) -> None:
    if not sys.stdin.isatty():
        raise SetupError("Run setup in an interactive Terminal so credentials can be entered privately.")
    settings = load_settings()
    values = settings.model_dump()
    if base_url is not None:
        values["base_url"] = base_url
    if model is not None:
        values["model"] = model
    try:
        settings = Settings.model_validate(values)
    except ValidationError:
        raise SetupError("Invalid settings. Use an HTTPS API base URL without a key, and a nonempty model ID.") from None
    settings.provider = "openai" if settings.base_url == OPENAI_BASE_URL else "openai-compatible"
    print("Next stores credentials only in your macOS login Keychain.")
    print("AI endpoint: " + settings.base_url)
    print("Model: " + settings.model)
    print("Task candidates will be sent to this endpoint when you request recommendations.")
    print("Leave an entry blank to keep its existing value.")
    # Refuse getpass's echoing fallback instead of accepting visible input.
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        for account, label in (("todoist", "Todoist API token"), (settings.ai_key_account, "AI provider API key")):
            value = getpass.getpass(label + ": ").strip()
            if value:
                set_secret(account, value)
    save_settings(settings)
    print("Preferences: " + str(CONFIG_PATH))
    print("Setup saved. Start with: python -m next_app")


async def check_provider(settings: Settings) -> None:
    """Verify the actual recommendation request format without reading Todoist."""
    from .ai import recommend
    from .models import Preferences, Task

    key = await asyncio.to_thread(get_secret, settings.ai_key_account)
    print("Checking " + settings.base_url + " with model " + settings.model)
    print("One synthetic task only; this request uses provider credits. No Todoist data is read.")
    await recommend(
        settings, key,
        [Task(id="next-connection-check", name="Write three ideas in a blank note")],
        Preferences(minutes=15, energy="low"),
    )
    print("Passed: Responses request with JSON-schema format accepted; output passed Next validation.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Next — your local, read-only task assistant")
    parser.add_argument("command", choices=["run", "setup", "check"], nargs="?", default="run")
    parser.add_argument("--base-url", help="HTTPS OpenAI-compatible API base URL (setup only)")
    parser.add_argument("--model", help="Exact provider model ID (setup only)")
    args = parser.parse_args()
    if args.command != "setup" and (args.base_url is not None or args.model is not None):
        parser.error("--base-url and --model are only used with setup")
    try:
        if args.command == "setup":
            setup(args.base_url, args.model)
            return
        if args.command == "check":
            asyncio.run(check_provider(load_settings()))
            return
        import uvicorn
        from .app import create_app
        settings = load_settings()
        print("Open http://127.0.0.1:{} — press Control-C to stop.".format(settings.port), flush=True)
        uvicorn.run(create_app(settings), host="127.0.0.1", port=settings.port, access_log=False)
    except (SetupError, ServiceError, getpass.GetPassWarning) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    except (KeyboardInterrupt, EOFError):
        print("\nStopped.")


if __name__ == "__main__":
    main()
