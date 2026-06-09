from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Settings


class RawArchive:
    def __init__(self, settings: Settings):
        self.settings = settings
        if self.settings.require_r2_archive and not self.settings.has_r2:
            raise RuntimeError("Cloudflare R2 archive is required, but R2 settings are incomplete")

    def write_json(self, source_slug: str, payload: Any, suffix: str = "response") -> str:
        now = datetime.now(timezone.utc)
        today = now.date().isoformat()
        timestamp = now.strftime("%H%M%S%f")
        key = f"raw/{source_slug}/{today}/{timestamp}-{suffix}.json"
        body = json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode("utf-8")

        if self.settings.has_r2_credentials:
            self._write_r2(key, body)
            return key

        if self.settings.has_r2_wrangler:
            self._write_r2_with_wrangler(key, body)
            return key

        path = self.settings.raw_archive_dir / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        return str(path)

    def _write_r2(self, key: str, body: bytes) -> None:
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError("boto3 is required for Cloudflare R2 writes") from exc

        client = boto3.client(
            "s3",
            endpoint_url=self.settings.r2_endpoint_url,
            region_name="auto",
            aws_access_key_id=self.settings.r2_access_key_id,
            aws_secret_access_key=self.settings.r2_secret_access_key,
        )
        client.put_object(
            Bucket=self.settings.r2_bucket_name,
            Key=key,
            Body=body,
            ContentType="application/json; charset=utf-8",
        )

    def _write_r2_with_wrangler(self, key: str, body: bytes) -> None:
        temp_dir = self.settings.raw_archive_dir / "_tmp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".json", dir=temp_dir) as temp_file:
                temp_file.write(body)
                temp_path = Path(temp_file.name)

            subprocess.run(
                self._wrangler_command()
                + [
                    "r2",
                    "object",
                    "put",
                    f"{self.settings.r2_bucket_name}/{key}",
                    "--file",
                    str(temp_path),
                    "--remote",
                ],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except FileNotFoundError as exc:
            raise RuntimeError("wrangler is required for R2_WRANGLER_UPLOAD writes") from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "").strip()
            raise RuntimeError(f"wrangler R2 upload failed: {detail}") from exc
        finally:
            if temp_path and temp_path.exists():
                temp_path.unlink()

    def _wrangler_command(self) -> list[str]:
        candidates = ["wrangler"]
        if os.name == "nt":
            candidates = ["wrangler.cmd", "wrangler.exe", "wrangler.ps1", "wrangler"]
        for candidate in candidates:
            path = shutil.which(candidate)
            if path:
                if path.lower().endswith(".ps1"):
                    return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path]
                return [path]

        appdata = os.environ.get("APPDATA")
        if os.name == "nt" and appdata:
            ps1_path = Path(appdata) / "npm" / "wrangler.ps1"
            if ps1_path.exists():
                return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1_path)]

        return ["wrangler"]
