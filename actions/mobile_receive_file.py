"""Receive an explicitly shared cloud file; never open or execute it automatically."""
import base64
import re
from pathlib import Path
import requests
from core.cloud_bridge import CloudBridge


def mobile_receive_file(parameters: dict, **_kwargs) -> str:
    file_id = str((parameters or {}).get("file_id", ""))
    if not re.fullmatch(r"[a-f0-9-]{36}", file_id):
        return "Invalid transfer id."
    bridge = CloudBridge()
    path = "/api/mobile/files"
    response = requests.get(bridge.cfg.url + path, params={"id": file_id},
                            headers=bridge._signed_headers("GET", path), timeout=20)
    if not response.ok:
        return "The shared file is unavailable."
    value = response.json()
    data = base64.b64decode(value["data"], validate=True)
    if len(data) > 3 * 1024 * 1024:
        return "Shared file exceeds 3 MB."
    name = re.sub(r"[^a-zA-Z0-9._ -]", "_", str(value.get("name") or "download"))[:120]
    folder = Path.home() / "Downloads" / "ArienX"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (file_id + "-" + name)
    if target.exists():
        return "File already received in Downloads/ArienX."
    with target.open("xb") as output:
        output.write(data)
    return "Shared file saved in Downloads/ArienX; it was not opened."

TOOL = {"name": "mobile_receive_file", "description": "Receive a file explicitly shared from ArienX Mobile into Downloads/ArienX. Does not execute the file.", "parameters": {"type": "OBJECT", "properties": {"file_id": {"type": "STRING"}}, "required": ["file_id"]}, "handler": mobile_receive_file}
