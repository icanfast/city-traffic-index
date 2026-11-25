import argparse
import gzip
import json
import os
import sys
from datetime import datetime, timezone

import boto3
import requests
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv

from collect_weather import collect_belgrade_weather


# Bounding boxes are west, south, east, north. Belgrade covers roughly 20 by 20 km.
CITY_BOUNDS = {
    "berlin": (13.268278, 52.419280, 13.561672, 52.599356),
    "belgrade": (20.320, 44.730, 20.575, 44.910),
}


def fetch_here(bounds, api_key):
    url = "https://data.traffic.hereapi.com/v7/flow"
    coordinates = ",".join(str(coordinate) for coordinate in bounds)
    params = {
        "in": f"bbox:{coordinates}",
        "locationReferencing": "shape",
        "apiKey": api_key,
    }

    try:
        response = requests.get(url, params=params, timeout=(10, 60), allow_redirects=False)
    except requests.RequestException as error:
        # A Requests exception may include the full URL and its API key.
        raise RuntimeError(f"HERE request failed ({type(error).__name__}).") from None
    if response.status_code != 200:
        raise RuntimeError(f"HERE returned HTTP {response.status_code}.")
    try:
        payload = response.json()
    except requests.exceptions.JSONDecodeError:
        raise RuntimeError("HERE returned invalid JSON.") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise RuntimeError("HERE response is missing its results list.")
    return payload


def upload_to_r2(s3, bucket, payload, collected_at):
    timestamp = collected_at.strftime("%Y-%m-%dT%H-%M-%SZ")
    key = f"here/{payload['city']}/{collected_at:%Y-%m-%d}/{timestamp}.json.gz"
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    compressed = gzip.compress(raw)
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=compressed,
        ContentType="application/json",
        ContentEncoding="gzip",
    )
    return key, len(raw), len(compressed)


def collect_city(city, api_key, s3, bucket):
    bounds = CITY_BOUNDS[city]
    collected_at = datetime.now(timezone.utc)
    here_data = fetch_here(bounds, api_key)
    payload = {
        "collectedAtUtc": collected_at.isoformat(),
        "city": city,
        "bbox": dict(zip(("west", "south", "east", "north"), bounds)),
        "here": here_data,
    }
    key, raw_size, compressed_size = upload_to_r2(s3, bucket, payload, collected_at)
    print(f"{city} HERE locations: {len(here_data['results'])}")
    print(f"Uploaded: {key}")
    print(f"Raw size: {raw_size / 1024:.1f} KiB")
    print(f"Gzip size: {compressed_size / 1024:.1f} KiB")


def main():
    parser = argparse.ArgumentParser(description="Archive Belgrade traffic and hourly weather in Cloudflare R2.")
    parser.add_argument("--city", choices=CITY_BOUNDS, help="Collect only traffic for the selected city.")
    args = parser.parse_args()

    load_dotenv()
    api_key = os.environ["HERE_API_KEY"]
    bucket = os.environ["R2_BUCKET"]
    s3 = boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )
    city = args.city or "belgrade"
    exit_code = 0
    traffic_uploaded = False
    try:
        collect_city(city, api_key, s3, bucket)
        traffic_uploaded = True
    except RuntimeError as error:
        print(f"{city} collection failed. {error}", file=sys.stderr)
        exit_code = 1
    except (BotoCoreError, ClientError) as error:
        print(f"{city} R2 upload failed ({type(error).__name__}).", file=sys.stderr)
        exit_code = 1

    if args.city is None:
        try:
            collect_belgrade_weather(s3, bucket)
        except (requests.RequestException, BotoCoreError, ClientError, ValueError) as error:
            print(f"Belgrade weather collection failed ({type(error).__name__}).", file=sys.stderr)
            exit_code = 1

    if traffic_uploaded and city == "belgrade" and os.getenv("GITHUB_ACTIONS") == "true":
        from publish_dashboard import publish_dashboard

        try:
            publish_dashboard(s3, bucket)
        except Exception as error:
            # Preserve collection status so the archive keeps its existing behavior.
            print(f"::warning::Dashboard refresh failed ({type(error).__name__}).", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
