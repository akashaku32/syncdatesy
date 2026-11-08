from pathlib import Path

from google.cloud import storage
from google.oauth2.credentials import Credentials

PROJECT_ID = "Datasyncher-QA-VM"
BUCKET_NAME = "meta_data_bucket_ds_development"

# Supply the newly generated WRITE-capable downscoped token.
DOWNSCOPED_TOKEN = "ya29.r.ARQiAB-EfcxmE5D-sKc1W4smoNPzK_7lG8RLksHVVexBUfu6kiknbbCw2bITcxPO-iYHex8O4xxmov6GnE-ZK9uNivfOojrsWztVy0uC2fRVbKCUPFlldyxZ38dCoiefhaC1m6baE4MopNRNwsuVYcgAurhAedL-uGfXj6ZVi3yyh-xjX_hJPLKjgc9CzHnFIRa_JE7D3QS2SJBll9v2iDYXtUHGebp-0kWbgzGRZzDAmioypQX3nW7VlxQa5W4koII2ylkV2RqEBeLSueXW_1cBVhUQERpoEZ9NpKaD5D_GgSGEqE4wynf2arJK53bSntSZH2fdYDlJTjdT4bIoWlri0r87Y9D4dRpqMpUBvJYKMk6LYDkguV2uNFulUGtKvSO0j35LQOrmjXJsM93Gzo1jI2Lmf5VjNwGy8HcrpE6foF-E5RvS48imISucj74t6qsFdDlYR28HQm_sRfdBMnq33s3rE8p1fceAxzTYe-HGsmUamEacP_AtaYGbZwm8AKvQ1DpUOLotvc0Pzp8LHjWb_cceOkqNID5g32OGRSVhLMXATU58hJdl7Ca4uqvdjIT_e33Syi9NCIyGOGupTrSeSSCIEGJpJaMK7Q5IefEW1SZJyLl-MyoNodAvpu9gnfTeyLXnTQNkmTRd0TSaL4v0MxEy8KvGHlfFTsPJ1fklEY5tsQfrffJfzXVAP_twbXLSjjYEBltXJsSk4NiJMnMhsl0Lm0oEqefrJCIMmPKsgnrt474lQpdmtJJ5OEJJcosnOvQ7laLSS-pPdP0ZzkW434kdZffm9U1cfvD842qYeoWWJKNtj7oDncNnN27LYf7p-KCFWIqvROBMzdLEFYDZq0ajrsWsAzr_pLmuI8ChP-DLFizog1VQqjIsmm4KmqI3nMbcfHFZEEiawtRP7tTHBPXepXAi_PxyS7fV6yuxWnhlLBf6y1qNJCQLjA"

FILES = ["page_1.parquet", "page_2.parquet"]

credentials = Credentials(token=DOWNSCOPED_TOKEN)

client = storage.Client(
    project=PROJECT_ID,
    credentials=credentials,
)

bucket = client.bucket(BUCKET_NAME)

# Use the directory containing this Python script.
script_dir = Path(__file__).resolve().parent

for filename in FILES:
    file_path = script_dir / filename

    if not file_path.is_file():
        print(f"File not found: {file_path}")
        continue

    blob = bucket.blob(f"reports/{filename}")

    blob.upload_from_filename(
        str(file_path),
        content_type="application/vnd.apache.parquet",
    )

    print(f"Uploaded {filename} to gs://{BUCKET_NAME}/reports/{filename}")

