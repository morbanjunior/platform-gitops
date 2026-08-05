#!/usr/bin/env python3
"""Set image tags in an environment values file without reformatting it.

    set_image_tag.py <values-file> <version> <component> [<component> ...]

Components are the keys under `components:` -- the same names the shared chart
in charts/app/ renders a Deployment and a Service for.

Why this exists instead of a one-line `yq` call:

`yq` parses YAML into a data structure and serialises it back. It keeps
comments, but blank lines are not data to it, so every write collapses the
spacing of the file. In a promotion pull request that turns a two-line change
into a nine-line diff, and a reviewer who has to hunt for the real change in a
wall of noise soon stops looking at all.

`sed` would preserve the file byte for byte, but it matches text: it depends on
the exact indentation and would silently do nothing if the file were ever
restructured. A deployment pipeline that fails silently is worse than one that
produces an ugly diff.

ruamel.yaml in round-trip mode gives both: it understands the document
structure (so it edits the right key, whatever the indentation) and preserves
comments, quoting and blank lines. Failure is loud -- a missing key raises.
"""

import sys

from ruamel.yaml import YAML


def main() -> int:
    if len(sys.argv) < 4:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2

    path, version, *components = sys.argv[1:]

    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    # Keep long values on one line; wrapping would show up as diff noise.
    yaml.width = 4096

    with open(path, encoding="utf-8") as handle:
        data = yaml.load(handle)

    for component in components:
        try:
            data["components"][component]["image"]["tag"] = version
        except (KeyError, TypeError) as error:
            print(
                f"error: {path} has no components.{component}.image.tag ({error})",
                file=sys.stderr,
            )
            return 1
        print(f"{path}: components.{component}.image.tag = {version}")

    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        yaml.dump(data, handle)

    return 0


if __name__ == "__main__":
    sys.exit(main())
