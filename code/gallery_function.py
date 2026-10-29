"""CampusSnaps gallery function (AWS Lambda).

S3 calls this function every time a photo lands in uploads/. It reads the
photo's details from S3 and saves them as one item in the DynamoDB table.
The website reads the gallery from that table.
"""
import os
import urllib.parse

import boto3

s3 = boto3.client("s3")
table = boto3.resource("dynamodb").Table(os.environ["TABLE"])


def handler(event, context):
    saved = 0
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        # Keys arrive URL-encoded in S3 events (a space becomes "+").
        key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
        head = s3.head_object(Bucket=bucket, Key=key)
        meta = head.get("Metadata", {})
        item = {
            "eventId": meta.get("event", "campus-life"),
            "photoId": key.split("/", 1)[-1],
            "s3Key": key,
            "caption": urllib.parse.unquote(meta.get("caption", "")),
            "sizeBytes": head["ContentLength"],
            "uploadedAt": head["LastModified"].isoformat(),
            "likes": 0,
        }
        try:
            # S3 can deliver the same event twice. The condition makes a repeat harmless.
            table.put_item(Item=item, ConditionExpression="attribute_not_exists(photoId)")
            saved += 1
            print("Saved", item["photoId"], "to event", item["eventId"])
        except table.meta.client.exceptions.ConditionalCheckFailedException:
            print("Already saved", item["photoId"])
    return {"saved": saved}
