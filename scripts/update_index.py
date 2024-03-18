# Gets shuttle id in the command line and updates the shuttle status
# uses argparse

import argparse
import json
import logging
import sys
import time
import urllib.parse
import urllib.request
from jsonschema import validate
from pathlib import Path

import yaml

# In legacy shuttles, the info.yaml files were committed after the fact, to a different path
LEGACY_SHUTTLES = ["tt02", "tt03", "tt03p5"]

parser = argparse.ArgumentParser(description="Update shuttle index")
parser.add_argument("shuttle_id", type=str, help="Shuttle ID")

args = parser.parse_args()


def normalize_pin_name(macro: str, name: str):
    if isinstance(name, int):
        return str(name)
    if isinstance(name, dict) or isinstance(name, list) or name is None:
        logging.warning(f"{macro}: invalid pin name {name}")
        return ""
    if name.lower() in ["-", "none", "unused", "not used"]:
        return ""
    return name


def convert_legacy_pinout(macro: str, is_mux: bool, project_info):
    pinout = {}
    inputs = project_info.get("inputs", [])
    outputs = project_info.get("outputs", [])
    bidirectional = project_info.get("bidirectional", [])
    if not isinstance(bidirectional, list):
        bidirectional = []
    input_name = "ui_in" if is_mux else "io_in"
    output_name = "uo_out" if is_mux else "io_out"
    for i in range(8):
        pinout[f"{input_name}[{i}]"] = (
            normalize_pin_name(macro, inputs[i]) if i < len(inputs) else ""
        )
    for i in range(8):
        pinout[f"{output_name}[{i}]"] = (
            normalize_pin_name(macro, outputs[i]) if i < len(outputs) else ""
        )
    if is_mux:
        for i in range(8):
            pinout[f"uio[{i}]"] = (
                normalize_pin_name(macro, bidirectional[i])
                if i < len(bidirectional)
                else ""
            )
    return pinout


def shuttle_index_url(repo: str, shuttle_id: str):
    if shuttle_id == "tt03":
        return "https://raw.githubusercontent.com/TinyTapeout/tinytapeout-03/main/project_info/index.json"
    if shuttle_id == "tt03p5":
        return "https://raw.githubusercontent.com/TinyTapeout/tinytapeout-03p5/main/project_info/mux_index.json"
    return f"https://tinytapeout.github.io/{repo}/shuttle_index.json"

root = Path(__file__).resolve().parents[1]
with open(root / "index" / "index.json") as f:
    shuttle_index = json.load(f)

shuttle = next(filter(lambda x: x["id"] == args.shuttle_id, shuttle_index["shuttles"]))
if not shuttle:
    print(f"Shuttle {args.shuttle_id} not found")
    sys.exit(1)

output_path = root / "index" / f"{shuttle['id']}.json"

# extract owner/repo from repo url, using a url parser module:
[owner, repo] = urllib.parse.urlparse(shuttle["repo"]).path[1:].split("/")
with urllib.request.urlopen(shuttle_index_url(repo, args.shuttle_id)) as f:
    index_json = json.load(f)

projects = []
cache_buster = int(time.time())
macro_addresses = {}
project_index = index_json["mux"] if "mux" in index_json else index_json["scanchain"]
for address, project_entry in project_index.items():
    macro = project_entry["macro"]
    if macro in macro_addresses:
        logging.warning(
            f"Duplicate macro {macro} at {address} (first at {macro_addresses[macro]})"
        )
        continue
    macro_addresses[macro] = address
    logging.info(f"Updating {macro} at {address}...")
    projects_dir = (
        "projects" if args.shuttle_id not in LEGACY_SHUTTLES else "project_info"
    )
    yaml_url = f"https://raw.githubusercontent.com/{owner}/{repo}/main/{projects_dir}/{macro}/info.yaml?token={cache_buster}"
    with urllib.request.urlopen(yaml_url) as f:
        project_yaml = yaml.safe_load(f)
    version = project_yaml.get("yaml_version", 1)
    project_info = (
        project_yaml["documentation"]
        if "documentation" in project_yaml
        else project_yaml["project"]
    )
    pinout = project_yaml.get("pinout", None)
    if not pinout:
        is_mux = version >= 3.5
        pinout = convert_legacy_pinout(macro, is_mux, project_info)
    projects.append(
        {
            "macro": macro,
            "address": int(address),
            "title": project_info["title"],
            "author": project_info["author"],
            "description": project_info["description"],
            "clock_hz": (
                project_info["clock_hz"]
                if isinstance(project_info.get("clock_hz", ""), int)
                else 0
            ),
            "tiles": project_entry.get("tiles", "1x1"),
            "analog_pins": project_entry.get("analog_pins", []),
            "repo": project_entry["repo"],
            "commit": project_entry.get("commit", ""),
            "pinout": pinout,
        }
    )

projects.sort(key=lambda x: x["macro"])

result = {
    "version": 3,
    "id": shuttle["id"],
    "name": shuttle["name"],
    "repo": shuttle["repo"],
    "commit": index_json["commit"],
    "projects": projects,
}

with open(root / "schemas" / "shuttle.schema.json") as f:
    schema = json.load(f)

validate(result, schema)

logging.info(f"Writing {output_path}")
with open(output_path, "w") as f:
    json.dump(result, f, indent=2)
