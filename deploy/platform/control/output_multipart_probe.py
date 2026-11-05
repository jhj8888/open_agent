"""Official boto3 inspection/abort, restricted to one generated fixture object key."""
import os
import re
import sys
import boto3
from botocore.config import Config

key=os.environ['FIXTURE_OBJECT_KEY']
assert re.fullmatch(r'task-outputs/v1/[a-f0-9-]{36}/[a-f0-9-]{36}/[a-f0-9-]{36}',key)
assert os.environ['FIXTURE_ENDPOINT']=='http://127.0.0.1:8333'
assert os.environ['FIXTURE_KEY'].startswith('fixture_')
client=boto3.client('s3',endpoint_url=os.environ['FIXTURE_ENDPOINT'],region_name='us-east-1',
    aws_access_key_id=os.environ['FIXTURE_KEY'],aws_secret_access_key=os.environ['FIXTURE_SECRET'],
    config=Config(connect_timeout=5,read_timeout=15,retries={'total_max_attempts':1},s3={'addressing_style':'path'}))
bucket='output-reconciliation-fixture'
result=client.list_multipart_uploads(Bucket=bucket,Prefix=key,MaxUploads=10)
assert not result.get('IsTruncated')
uploads=result.get('Uploads',[])
if sys.argv[1]=='absent':
    assert not uploads
    print('OUTPUT_MULTIPART_OK=absent',flush=True)
    sys.exit(0)
assert len(uploads)==1 and uploads[0]['Key']==key
upload=uploads[0]['UploadId']
parts=client.list_parts(Bucket=bucket,Key=key,UploadId=upload,MaxParts=10)
assert not parts.get('IsTruncated') and sum(part['Size'] for part in parts['Parts'])==5242880
if sys.argv[1]=='abort':
    client.abort_multipart_upload(Bucket=bucket,Key=key,UploadId=upload)
    result=client.list_multipart_uploads(Bucket=bucket,Prefix=key,MaxUploads=10)
    assert not result.get('IsTruncated') and not result.get('Uploads')
else:assert sys.argv[1]=='present'
print('OUTPUT_MULTIPART_OK='+sys.argv[1],flush=True)
