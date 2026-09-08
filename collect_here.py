import gzip
import json
import os
from datetime import datetime, timezone

import boto3
import requests

from dotenv import load_dotenv

load_dotenv()


HERE_API_KEY = os.environ["HERE_API_KEY"]

R2_ENDPOINT = os.environ["R2_ENDPOINT"]
R2_BUCKET = os.environ["R2_BUCKET"]
R2_ACCESS_KEY_ID = os.environ["R2_ACCESS_KEY_ID"]
R2_SECRET_ACCESS_KEY = os.environ["R2_SECRET_ACCESS_KEY"]


# Berlin rectangle
WEST = 13.268278
SOUTH = 52.419280
EAST = 13.561672
NORTH = 52.599356


def fetch_here():
    url = "https://data.traffic.hereapi.com/v7/flow"

    params = {
        "in": f"bbox:{WEST},{SOUTH},{EAST},{NORTH}",
        "locationReferencing": "shape",
        "apiKey": HERE_API_KEY,
    }

    response = requests.get(url, params=params, timeout=120)
    response.raise_for_status()

    return response.json()


def upload_to_r2(payload, collected_at):
    s3 = boto3.client(
        "s3",
        endpoint_url=R2_ENDPOINT,
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        region_name="auto",
    )

    timestamp = collected_at.strftime("%Y-%m-%dT%H-%M-%SZ")

    key = (
        f"here/berlin/"
        f"{collected_at:%Y-%m-%d}/"
        f"{timestamp}.json.gz"
    )

    raw = json.dumps(
        payload,
        separators=(",", ":"),
    ).encode("utf-8")

    compressed = gzip.compress(raw)

    s3.put_object(
        Bucket=R2_BUCKET,
        Key=key,
        Body=compressed,
        ContentType="application/json",
        ContentEncoding="gzip",
    )

    return key, len(raw), len(compressed)


def main():
    collected_at = datetime.now(timezone.utc)

    here_data = fetch_here()

    payload = {
        "collectedAtUtc": collected_at.isoformat(),
        "city": "berlin",
        "bbox": {
            "west": WEST,
            "south": SOUTH,
            "east": EAST,
            "north": NORTH,
        },
        "here": here_data,
    }

    key, raw_size, compressed_size = upload_to_r2(
        payload,
        collected_at,
    )

    print(f"HERE locations: {len(here_data.get('results', []))}")
    print(f"Uploaded: {key}")
    print(f"Raw size: {raw_size / 1024:.1f} KiB")
    print(f"Gzip size: {compressed_size / 1024:.1f} KiB")


if __name__ == "__main__":
    main()