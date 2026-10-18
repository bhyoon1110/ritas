"""Create a standalone USB archive, with no source code, credentials or user data."""
import argparse
from pathlib import Path
import tarfile

from offline_report_preview import BUNDLE, sha256, verify_bundle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="New .tar.gz file in an existing directory")
    args = parser.parse_args()
    output = args.output.resolve()
    checksum = output.with_name(output.name + ".sha256")
    if output.exists() or checksum.exists() or not output.name.endswith(".tar.gz"):
        raise SystemExit("Use a new .tar.gz output path; existing files are not overwritten")
    verify_bundle(BUNDLE)
    deploy = Path(__file__).resolve().parent
    files = {"README.md": deploy / "OFFLINE_REPORT_PREVIEW.md"}
    for name in ("install_offline_report_preview.sh", "offline_report_preview.py"):
        files["deploy/" + name] = deploy / name
    for path in sorted(BUNDLE.rglob("*")):
        if path.is_file():
            files["deploy/offline/report-preview-noble-arm64/" + str(path.relative_to(BUNDLE))] = path
    with tarfile.open(output, "x:gz") as archive:
        for name, path in files.items():
            if path.is_symlink():
                raise ValueError("Symlink is not allowed")
            archive.add(path, arcname="rist-report-preview-offline/" + name, recursive=False)
    with checksum.open("x") as stream:
        stream.write(f"{sha256(output)}  {output.name}\n")
    print(f"Created {output}: {output.stat().st_size} bytes; {len(files)} files")
    print(checksum)


if __name__ == "__main__":
    main()
