"""Build the optional static dashboard data as part of the existing collection run."""

import hashlib
import io
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


RUNTIME_SHA256 = "762a078a3faafd9491b05ad675821cade53751581c9f1f268ee8d85fcb04e26b"
RUNTIME_KEY = f"forecast/belgrade/v1/runtime-{RUNTIME_SHA256}.zip"


def publish_dashboard(s3, bucket):
    # The release is private, and the digest pins the executable bundle.
    with s3.get_object(Bucket=bucket, Key=RUNTIME_KEY)["Body"] as stream:
        bundle = stream.read()
    if hashlib.sha256(bundle).hexdigest() != RUNTIME_SHA256:
        raise ValueError("Unexpected dashboard runtime digest")

    job_env = os.environ.copy()
    job_env.pop("HERE_API_KEY", None)
    job_env["BELGRADE_RUNTIME_SHA256"] = RUNTIME_SHA256
    job_env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    job_env["PYTHONUNBUFFERED"] = "1"
    install_env = {key: value for key, value in job_env.items() if not key.startswith("R2_")}
    with tempfile.TemporaryDirectory(prefix="dashboard-release-") as temp:
        release = Path(temp)
        with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
            archive.extractall(release)
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", "--only-binary=:all:",
             "-r", str(release / "requirements.txt")],
            env=install_env, check=True, timeout=180,
        )
        subprocess.run(
            [sys.executable, str(release / "publish_feed.py")],
            env=job_env, check=True, timeout=60,
        )
