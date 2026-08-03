#!/usr/bin/env python3
"""Maintenance for the platform's sealed database credentials.

    secrets.py list
    secrets.py check
    secrets.py rotate <app> <environment>|--all-envs [--stdin]
    secrets.py reseal [<app>] [<environment>]
    secrets.py recover <app> <environment> --key <backup-file>

Two different things can change, and they need different answers:

  * The PASSWORD changed -> `rotate`. Needs the new value.
  * The SEALING KEY changed -> `reseal`. Needs nothing: kubeseal --re-encrypt
    asks the controller to decrypt with the old key and encrypt with the new
    one, so no plaintext is ever handled.

That second case is the one nobody plans for. The controller rotates its own
key every 30 days and keeps the old ones for decryption, so nothing breaks
immediately -- but the ciphertext in this repository stays pinned to older and
older keys. Lose one (an incomplete restore is enough) and those values are
gone. `check` is what surfaces that before a pod does.

Applications are discovered by walking apps/*/envs/*.yaml. There is no list of
applications here: like the root Application and the validate workflow, this
operates on a convention, not an inventory.
"""

import argparse
import base64
import getpass
import subprocess
import sys
from pathlib import Path

from ruamel.yaml import YAML

REPO_ROOT = Path(__file__).resolve().parent.parent
APPS_DIR = REPO_ROOT / "apps"

CONTROLLER_NAME = "sealed-secrets-controller"
CONTROLLER_NAMESPACE = "kube-system"


def yaml_handler() -> YAML:
    """Round-trip YAML, configured to leave the file as it found it.

    Same settings as set_image_tag.py. It matters more here: a noisy diff on a
    credential change is exactly where you do not want a reviewer to stop
    looking.
    """
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.width = 4096
    return yaml


def read_password_secret(path: Path) -> dict:
    """Return the database.passwordSecret block of a values file, if any."""
    if not path.is_file():
        return {}
    data = yaml_handler().load(path.read_text(encoding="utf-8")) or {}
    return ((data.get("database") or {}).get("passwordSecret")) or {}


class Target:
    """One (application, environment) pair and the values it carries.

    Values are layered the same way Helm layers them: chart defaults first, the
    environment file on top. The secret's name and key live in the chart (they
    are the same everywhere); the namespace and the ciphertext live in the
    environment file, because they differ per environment.

    Reading only the environment file would leave the name empty -- and with
    strict scope the ciphertext is bound to namespace AND name, so every
    decryption would fail for a reason that looks like a key problem.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.app = path.parent.parent.name
        self.environment = path.stem

        chart_defaults = read_password_secret(path.parent.parent / "chart" / "values.yaml")
        data = yaml_handler().load(path.read_text(encoding="utf-8")) or {}
        env_secret = ((data.get("database") or {}).get("passwordSecret")) or {}

        self.namespace = (data.get("namespace") or {}).get("name") or ""
        self.secret_name = env_secret.get("name") or chart_defaults.get("name") or ""
        self.secret_key = env_secret.get("key") or chart_defaults.get("key") or "password"
        self.encrypted = env_secret.get("encrypted") or ""

    def __str__(self) -> str:
        return f"{self.app}/{self.environment}"

    @property
    def usable(self) -> bool:
        return bool(self.namespace and self.secret_name)

    def sealed_secret_manifest(self) -> str:
        """Rebuild the SealedSecret exactly as the chart renders it.

        kubeseal works on whole manifests, and the chart owns the real one, so
        the alternative would be shelling out to helm template and parsing the
        result -- slower and coupled to the chart's layout.
        """
        return (
            "apiVersion: bitnami.com/v1alpha1\n"
            "kind: SealedSecret\n"
            "metadata:\n"
            f"  name: {self.secret_name}\n"
            f"  namespace: {self.namespace}\n"
            "spec:\n"
            "  encryptedData:\n"
            f"    {self.secret_key}: {self.encrypted}\n"
            "  template:\n"
            "    metadata:\n"
            f"      name: {self.secret_name}\n"
            f"      namespace: {self.namespace}\n"
            "    type: Opaque\n"
        )

    def write_encrypted(self, ciphertext: str) -> None:
        yaml = yaml_handler()
        data = yaml.load(self.path.read_text(encoding="utf-8"))
        data["database"]["passwordSecret"]["encrypted"] = ciphertext
        with self.path.open("w", encoding="utf-8", newline="\n") as handle:
            yaml.dump(data, handle)
        self.encrypted = ciphertext


def discover(app: str | None = None, environment: str | None = None) -> list[Target]:
    targets = []
    for env_file in sorted(APPS_DIR.glob("*/envs/*.yaml")):
        target = Target(env_file)
        if app and target.app != app:
            continue
        if environment and target.environment != environment:
            continue
        targets.append(target)
    return targets


def run_kubeseal(args: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "kubeseal",
            "--controller-name",
            CONTROLLER_NAME,
            "--controller-namespace",
            CONTROLLER_NAMESPACE,
            *args,
        ],
        input=stdin,
        capture_output=True,
        text=True,
        # Explicit UTF-8: the default is the locale encoding, which is cp1252
        # on Windows and cannot represent a password with accents or symbols.
        encoding="utf-8",
    )


def seal(value: str, namespace: str, name: str, key: str) -> str:
    """Encrypt one value for one namespace/name pair.

    Builds the Secret in memory and pipes it to kubeseal, the same shape as
    `kubectl create secret --dry-run=client -o yaml | kubeseal`. The plaintext
    never touches disk and never appears in a command line, so it stays out of
    the process list and out of the shell history.

    Not `--raw --from-file=key=/dev/stdin`: that path does not exist on Windows,
    and writing to a temporary file would put the password on disk.

    Strict scope (the default) binds the ciphertext to namespace AND name, so a
    value sealed for a development namespace cannot be replayed into production.
    """
    secret = (
        "apiVersion: v1\n"
        "kind: Secret\n"
        "metadata:\n"
        f"  name: {name}\n"
        f"  namespace: {namespace}\n"
        "type: Opaque\n"
        "data:\n"
        f"  {key}: {base64.b64encode(value.encode('utf-8')).decode('ascii')}\n"
    )

    result = run_kubeseal(["--format", "yaml"], stdin=secret)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())

    sealed = yaml_handler().load(result.stdout)
    try:
        return sealed["spec"]["encryptedData"][key]
    except (KeyError, TypeError) as error:
        raise RuntimeError(f"unexpected kubeseal output ({error})") from error


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def cmd_list(_: argparse.Namespace) -> int:
    targets = discover()
    if not targets:
        print("no environments found under apps/*/envs/", file=sys.stderr)
        return 1

    print(f"{'APP':<10} {'ENV':<10} {'NAMESPACE':<18} {'SECRET':<24} SEALED")
    for t in targets:
        state = f"yes ({len(t.encrypted)} chars)" if t.encrypted else "NO"
        print(f"{t.app:<10} {t.environment:<10} {t.namespace:<18} {t.secret_name:<24} {state}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Ask the controller whether each blob can still be decrypted.

    This is the answer to "are my secrets still valid?" -- before a pod answers
    it with CreateContainerConfigError. Run it after restoring a backup, after
    rebuilding a cluster, or periodically.

    It cannot run in CI: it needs cluster access, which is precisely what this
    platform's pipelines deliberately do not have. Automating it would mean a
    CronJob inside the cluster.
    """
    targets = discover(args.app, args.environment)
    failed = 0

    for t in targets:
        if not t.encrypted:
            print(f"SKIP   {t}: no sealed value")
            continue
        result = run_kubeseal(["--validate"], stdin=t.sealed_secret_manifest())
        if result.returncode == 0:
            print(f"OK     {t}")
        else:
            print(f"FAILED {t}: {result.stderr.strip()}")
            failed += 1

    if failed:
        print(f"\n{failed} value(s) cannot be decrypted by this cluster.")
        print("If the sealing key was rotated or restored, run: secrets.py reseal")
    return 1 if failed else 0


def cmd_rotate(args: argparse.Namespace) -> int:
    if not args.all_envs and not args.environment:
        print("error: give an environment or --all-envs", file=sys.stderr)
        return 2

    targets = discover(args.app, None if args.all_envs else args.environment)
    if not targets:
        print(f"error: nothing matches {args.app}/{args.environment or '*'}", file=sys.stderr)
        return 1

    if args.stdin:
        # lstrip the BOM: PowerShell pipes UTF-8-with-BOM, and that invisible
        # character would become part of the password.
        value = sys.stdin.read().lstrip("﻿").strip()
    else:
        # No echo, and never an argument: a password on the command line ends
        # up in the shell history in plaintext.
        value = getpass.getpass("New password: ")
        if value != getpass.getpass("Confirm: "):
            print("error: values do not match", file=sys.stderr)
            return 1
    if not value:
        print("error: empty value", file=sys.stderr)
        return 1

    for t in targets:
        if not t.usable:
            print(f"SKIP   {t}: no namespace or secret name")
            continue
        try:
            t.write_encrypted(seal(value, t.namespace, t.secret_name, t.secret_key))
        except RuntimeError as error:
            print(f"FAILED {t}: {error}", file=sys.stderr)
            return 1
        print(f"SEALED {t} -> {t.path.relative_to(REPO_ROOT)}")

    print("\nEvery environment gets a different ciphertext for the same password:")
    print("the encryption is bound to namespace and name.")
    print("\nNext:")
    print("  1. Change the password in the database itself -- this script does not.")
    print("  2. git add/commit/push (pull request for production).")
    print("  3. kubectl rollout restart deployment -n <namespace>")
    print("     Pods read secretKeyRef at container start, so a running pod keeps the old value.")
    return 0


def cmd_reseal(args: argparse.Namespace) -> int:
    """Re-encrypt with the cluster's current key, without the plaintext.

    kubeseal --re-encrypt sends the sealed secret to the controller, which
    decrypts it with whichever key it was sealed under and encrypts it with the
    latest one. Nothing here ever sees the password.

    Run it after the controller rotates its key, or as hygiene so no value
    stays pinned to an old key that a partial restore could lose.
    """
    targets = [t for t in discover(args.app, args.environment) if t.encrypted]
    if not targets:
        print("nothing to reseal")
        return 0

    changed = 0
    for t in targets:
        result = run_kubeseal(["--re-encrypt", "-o", "yaml"], stdin=t.sealed_secret_manifest())
        if result.returncode != 0:
            print(f"FAILED {t}: {result.stderr.strip()}", file=sys.stderr)
            return 1

        data = yaml_handler().load(result.stdout)
        new_value = data["spec"]["encryptedData"][t.secret_key]

        if new_value == t.encrypted:
            print(f"SAME   {t}: already on the current key")
            continue

        t.write_encrypted(new_value)
        changed += 1
        print(f"RESEAL {t} -> {t.path.relative_to(REPO_ROOT)}")

    if changed:
        print(f"\n{changed} value(s) re-encrypted with the current key.")
        print("The passwords did not change -- only the key they are sealed with.")
        print("Commit and push; the controller keeps serving the same Secret meanwhile.")
    return 0


def cmd_recover(args: argparse.Namespace) -> int:
    """Disaster mode: decrypt using the backed-up private key.

    Answers "what was the production password?" when the cluster is gone.
    """
    key_file = Path(args.key)
    if not key_file.is_file():
        print(f"error: {key_file} not found", file=sys.stderr)
        return 1

    targets = [t for t in discover(args.app, args.environment) if t.encrypted]
    if not targets:
        print(f"error: nothing sealed for {args.app}/{args.environment}", file=sys.stderr)
        return 1

    print("WARNING: this prints secrets in plaintext to your terminal.\n", file=sys.stderr)

    for t in targets:
        result = run_kubeseal(
            ["--recovery-unseal", "--recovery-private-key", str(key_file), "-o", "yaml"],
            stdin=t.sealed_secret_manifest(),
        )
        if result.returncode != 0:
            print(f"FAILED {t}: {result.stderr.strip()}", file=sys.stderr)
            return 1

        data = yaml_handler().load(result.stdout)
        import base64

        value = base64.b64decode(data["data"][t.secret_key]).decode()
        print(f"{t}: {value}")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Maintain the platform's sealed database credentials.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="show every application/environment and whether it is sealed")

    p_check = sub.add_parser("check", help="verify every value can be decrypted by this cluster")
    p_check.add_argument("app", nargs="?")
    p_check.add_argument("environment", nargs="?")

    p_rotate = sub.add_parser("rotate", help="seal a new password value")
    p_rotate.add_argument("app")
    p_rotate.add_argument("environment", nargs="?")
    p_rotate.add_argument(
        "--all-envs", action="store_true", help="apply to every environment of the application"
    )
    p_rotate.add_argument(
        "--stdin", action="store_true", help="read the value from stdin instead of prompting"
    )

    p_reseal = sub.add_parser("reseal", help="re-encrypt with the cluster's current key")
    p_reseal.add_argument("app", nargs="?")
    p_reseal.add_argument("environment", nargs="?")

    p_recover = sub.add_parser("recover", help="decrypt using a backed-up private key")
    p_recover.add_argument("app")
    p_recover.add_argument("environment", nargs="?")
    p_recover.add_argument("--key", required=True, help="path to the sealing key backup")

    args = parser.parse_args()

    commands = {
        "list": cmd_list,
        "check": cmd_check,
        "rotate": cmd_rotate,
        "reseal": cmd_reseal,
        "recover": cmd_recover,
    }
    try:
        return commands[args.command](args)
    except FileNotFoundError:
        print("error: kubeseal not found on PATH", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
