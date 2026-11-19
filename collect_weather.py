import gzip
import json
from datetime import datetime, timezone

import requests
from botocore.exceptions import ClientError
from requests.adapters import HTTPAdapter
from urllib3.util import Retry


FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
CURRENT_FIELDS = "temperature_2m,precipitation,snowfall"
FORECAST_FIELDS = f"{CURRENT_FIELDS},precipitation_probability"
REQUEST_PARAMS = {
    "latitude": 44.82,
    "longitude": 20.45,
    "timezone": "GMT",
    "timeformat": "unixtime",
    "current": CURRENT_FIELDS,
    "hourly": FORECAST_FIELDS,
    "past_hours": 2,
    "forecast_hours": 4,
}


def collect_belgrade_weather(s3, bucket):
    """Archive the first successful forecast fetched in each UTC hour."""
    hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    key = f"weather/open-meteo/belgrade/{hour:%Y-%m-%d}/{hour:%Y-%m-%dT%H-00-00Z}.json.gz"
    try:
        s3.head_object(Bucket=bucket, Key=key)
    except ClientError as error:
        if error.response["ResponseMetadata"]["HTTPStatusCode"] != 404:
            raise
    else:
        print("Belgrade weather is already archived for this hour.")
        return

    with requests.Session() as session:
        retries = Retry(total=1, connect=1, read=1, status=0)
        session.mount("https://", HTTPAdapter(max_retries=retries))
        response = session.get(FORECAST_URL, params=REQUEST_PARAMS, timeout=(5, 20))
        fetched_at = datetime.now(timezone.utc)
        response.raise_for_status()
        weather = response.json()
    if not isinstance(weather, dict):
        raise ValueError("Expected an Open-Meteo forecast object.")
    current = weather.get("current")
    hourly = weather.get("hourly")
    if not isinstance(current, dict) or not isinstance(hourly, dict):
        raise ValueError("Open-Meteo response is missing current or hourly weather.")
    times = hourly.get("time")
    if not isinstance(times, list) or not times or not isinstance(current.get("time"), int):
        raise ValueError("Open-Meteo response is missing weather timestamps.")
    if any(field not in current for field in CURRENT_FIELDS.split(",")) or any(
        not isinstance(hourly.get(field), list) or len(hourly[field]) != len(times)
        for field in FORECAST_FIELDS.split(",")
    ):
        raise ValueError("Open-Meteo response is missing weather fields or timestamps.")

    capture = {
        "schema_version": 1,
        "provider": "open-meteo",
        "city": "belgrade",
        "fetched_at_utc": fetched_at.isoformat(),
        "request": REQUEST_PARAMS,
        "response": weather,
    }
    try:
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=gzip.compress(json.dumps(capture, separators=(",", ":")).encode("utf-8")),
            ContentType="application/json",
            ContentEncoding="gzip",
            IfNoneMatch="*",
        )
    except ClientError as error:
        if error.response["ResponseMetadata"]["HTTPStatusCode"] != 412:
            raise
        print("Belgrade weather was already archived by another run.")
    else:
        print(f"Uploaded: {key}")
