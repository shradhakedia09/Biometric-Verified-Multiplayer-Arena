import csv  #reads data
import time #for sleep timer between pass 1 and retry query ka pass
import requests #HTTP GET reuqests to fetch iamges 
from PIL import Image #validates the jpg
import io #trate raw bytes as a file (needed ny the boilerplate given (pillows))
import mysql.connector #talks to MySQL
from pymongo import MongoClient #talks to MongoDB
from bson.binary import Binary
from dotenv import load_dotenv
import os

load_dotenv()

REQUEST_TIMEOUT = 10


#db setup
# (called once in the beginning of main to get a live connection to each database)
def get_mysql_conn():
    return mysql.connector.connect(
        host=os.getenv("DB_HOST"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASS"),
        database=os.getenv("DB_NAME"),
    )

def get_mongo_client():
    return MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))


#core operations 
# hits the url and returns (images_bytes, status) 
# Status: ok: 200 response with content, not_found: 404, bad URL, anything permanent, transient: retrying query condition
def fetch_image(url: str):
    if not url:
        print(f"[WARN] Empty URL")
        return None, "not_found"
    
    try:
        if url.startswith("http://") or url.startswith("https://"):
            full_url = f"{url.rstrip('/')}/images/pfp.jpg"
        else:
            full_url = f"https://{url.rstrip('/')}/images/pfp.jpg"
        resp = requests.get(full_url, timeout=REQUEST_TIMEOUT)

    except requests.exceptions.Timeout:
        print(f"[WARN] Timeout for {url}")
        return None, "transient"
    
    except requests.exceptions.ConnectionError as e:
        print(f"[WARN] Connection error for {url}: {e}")
        return None, "transient"
    
    except requests.exceptions.RequestException as e:
        print(f"[WARN] Request error for {url}: {e}")
        return None, "transient"

    if resp.status_code == 404:
        print(f"[INFO] 404 for {url}")
        return None, "not_found"

    if resp.status_code != 200:
        print(f"[WARN] Unexpected status {resp.status_code} for {url} — treating as transient")
        return None, "transient"

    return resp.content, "ok"

# first checks magic bytes: \xff\xd8\xff- if those aren't there its not JPG
# now catche scorrupt files by calling pillow  
def is_valid_jpg(content: bytes) -> bool:
    """Returns True if bytes are a decodable image."""
    if content[:3] != b"\xff\xd8\xff":
        return False
    try:
        img = Image.open(io.BytesIO(content))
        img.verify()
        return True
    except Exception:
        return False


def to_canonical_jpg(content: bytes) -> bytes | None:
    """Re-encode bytes as canonical RGB JPEG for downstream face recognition."""
    try:
        img = Image.open(io.BytesIO(content)).convert("RGB")
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=95)
        return out.getvalue()
    except Exception:
        return None

# sudent data in MySQL. 
# uses ON DUPLCIATE KEY UPDATE so reruns are safe 
#True: success, False: failure
def insert_mysql(conn, uid: str, name: str):
    """Upsert student metadata into MySQL."""
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO users (uid, name, elo_rating, is_online)
            VALUES (%s, %s, 1200, FALSE)
            ON DUPLICATE KEY UPDATE name = VALUES(name)
            """,
            (uid, name),
        )
        conn.commit()
        print(f"[MySQL OK] {uid}")
        return True
    except Exception as e:
        print(f"[MySQL FAILED] {uid}: {e}")
        conn.rollback()
        return False
    finally:
        cursor.close()

#stored images keyed by uid(key val pair like in a dict)
#if exists: overwrite nhi toh logs into MySQL only 
def upsert_mongo(collection, uid: str, image_bytes: bytes):
    """Upsert image keyed by uid into MongoDB."""
    try:
        collection.update_one(
            {"uid": uid},
            {"$set": {"uid": uid, "image": Binary(image_bytes)}},
            upsert=True,
        )
        print(f"[MongoDB OK] {uid}")

    except Exception as e:
        print(f"[MongoDB FAILED] {uid}: {e} — MySQL row kept")


def delete_mongo_image(collection, uid: str):
    """Delete stale Mongo image for uid when latest scrape has no valid JPG."""
    try:
        collection.delete_one({"uid": uid})
    except Exception as e:
        print(f"[MongoDB DELETE FAILED] {uid}: {e}")


#  main pipeline 

#for one student the main logic is here :
# call fetch_image -> if transient: return false (retry queue, nothing written yet) -> else write MySQL first -> if image okay and valid JPG then wirte to mongodb also-> return true for everything else 
def process_student(conn, collection, row: dict) -> bool:
    """
    Returns False if transient error (add to retry queue).
    Returns True if permanent fail or success.
    """
    uid = row.get("uid", "").strip()
    name = row.get("name", "").strip()
    url = row.get("website_url", "").strip()
 
    image_bytes, status = fetch_image(url)
 
    if status == "transient":
        return False  # retry later, don't write anything yet
 
    # permanent failure or success — always write MySQL first
    if not insert_mysql(conn, uid, name):
        return True  # MySQL failed, log and move on
 
    if status == "not_found":
        print(f"[No image] {uid} — MySQL only")
        delete_mongo_image(collection, uid)
        return True
 
    # status == "ok" — validate jpg before writing MongoDB
    if not is_valid_jpg(image_bytes):
        print(f"[Invalid JPG] {uid} — MySQL only")
        delete_mongo_image(collection, uid)
        return True

    canonical_image = to_canonical_jpg(image_bytes)
    if canonical_image is None:
        print(f"[Canonicalization FAILED] {uid} — MySQL only")
        delete_mongo_image(collection, uid)
        return True

    upsert_mongo(collection, uid, canonical_image)
    return True

def main():
    retry_queue = []
    failed_log = []

    conn = get_mysql_conn()
    mongo_client = get_mongo_client()
    collection = mongo_client[os.getenv("MONGO_DB", "arena")]["profile_images"]

    script_dir = os.path.dirname(os.path.abspath(__file__))
    csv_path = os.path.join(script_dir, "batch_data.csv")
    with open(csv_path) as f:        
        reader = csv.DictReader(f, fieldnames=["uid", "name", "website_url"])
        for row in reader:
            success = process_student(conn, collection, row)
            if not success:
                retry_queue.append(row)    

    # retry pass
    time.sleep(8)
    for row in retry_queue:
        uid = row.get("uid", "").strip()
        success = process_student(conn, collection, row)
        if not success:
            print(f"[Retry FAILED] {uid} — writing MySQL only")
            insert_mysql(conn, uid, row.get("name", "").strip())
            failed_log.append(uid)

    print(f"Done. Failed UIDs: {failed_log}")

    conn.close()
    mongo_client.close()

if __name__ == "__main__":
    main()