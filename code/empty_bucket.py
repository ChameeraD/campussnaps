"""Clean-up helper (a CloudFormation custom resource).

S3 refuses to delete a bucket that still has photos in it. When you delete
the stack, CloudFormation calls this function first, and it empties the
photo bucket so the whole stack can be deleted in one step.
"""
import json
import urllib.request

import boto3

s3 = boto3.client("s3")


def send(event, context, status, reason):
    body = json.dumps({
        "Status": status,
        "Reason": reason or "See CloudWatch Logs: " + context.log_stream_name,
        "PhysicalResourceId": event.get("PhysicalResourceId", "empty-bucket-on-delete"),
        "StackId": event["StackId"],
        "RequestId": event["RequestId"],
        "LogicalResourceId": event["LogicalResourceId"],
        "Data": {},
    }).encode()
    request = urllib.request.Request(
        event["ResponseURL"], data=body, method="PUT",
        headers={"Content-Type": "", "Content-Length": str(len(body))},
    )
    urllib.request.urlopen(request, timeout=10)


def handler(event, context):
    status, reason = "SUCCESS", ""
    try:
        if event["RequestType"] == "Delete":
            bucket = event["ResourceProperties"]["BucketName"]
            deleted = 0
            for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
                objects = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
                if objects:
                    s3.delete_objects(Bucket=bucket, Delete={"Objects": objects, "Quiet": True})
                    deleted += len(objects)
            print("Deleted", deleted, "objects from", bucket)
    except s3.exceptions.NoSuchBucket:
        print("Bucket is already gone")
    except Exception as err:
        print("Could not empty the bucket:", repr(err))
        status, reason = "FAILED", "Could not empty the bucket: " + str(err)[:200]
    send(event, context, status, reason)
