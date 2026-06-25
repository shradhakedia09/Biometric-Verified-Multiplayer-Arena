import os
import logging
import threading
from typing import Optional

from pymongo import MongoClient
from pymongo.errors import PyMongoError
import mysql.connector
from mysql.connector import Error as MySQLError
from dotenv import load_dotenv


try:
    from .facial_recognition_module import find_closest_match, get_face_encoding
    _FACE_IMPORT_ERROR = None
except Exception as import_error:
    try:
        from facial_recognition_module import find_closest_match, get_face_encoding
        _FACE_IMPORT_ERROR = None
    except Exception:
        find_closest_match = None
        get_face_encoding = None
        _FACE_IMPORT_ERROR = import_error

load_dotenv()
logger = logging.getLogger(__name__)
_AUTH_STATUS = threading.local()
_encodings_cache: dict = {}


def _set_last_auth_error(message: str) -> None:
    _AUTH_STATUS.last_error = message


def get_last_auth_error() -> str:
    return getattr(_AUTH_STATUS, "last_error", "")


def build_encodings_cache_at_startup() -> None:
    global _encodings_cache
    if get_face_encoding is None:
        logger.error("Cannot build cache: facial_recognition_module not available")
        return
    try:
        mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017")
        mongo_db = os.getenv("MONGO_DB", "arena")
        mongo_client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
        collection = mongo_client[mongo_db]["profile_images"]

        db_images_dict = {}
        for doc in collection.find({}, {"uid": 1, "image": 1, "_id": 0}):
            uid = doc.get("uid")
            raw_image = doc.get("image")
            if not uid or raw_image is None:
                continue
            if isinstance(raw_image, memoryview):
                db_images_dict[uid] = raw_image.tobytes()
            elif isinstance(raw_image, (bytes, bytearray)):
                db_images_dict[uid] = bytes(raw_image)
            elif isinstance(raw_image, str):
                db_images_dict[uid] = raw_image.split(",", 1)[1] if raw_image.startswith("data:") and "," in raw_image else raw_image
            else:
                try:
                    db_images_dict[uid] = bytes(raw_image)
                except Exception:
                    logger.warning("Skipping uid %s: unsupported image type %s", uid, type(raw_image).__name__)
                    continue

        mongo_client.close()
        logger.info("Fetched %d images from MongoDB", len(db_images_dict))

        cache = {}
        invalid_count = 0
        for uid, image_data in db_images_dict.items():
            try:
                encoding = get_face_encoding(image_data)
            except Exception:
                encoding = None
            if encoding is not None and getattr(encoding, "shape", None) == (128,):
                cache[uid] = encoding
            else:
                invalid_count += 1

        _encodings_cache = cache
        logger.info("Encodings cache ready: %d faces (%d skipped)", len(_encodings_cache), invalid_count)

    except Exception as e:
        logger.error("Failed to build encodings cache: %s", e)


def authenticate_face(login_image: bytes) -> Optional[str]:
    _set_last_auth_error("")

    if find_closest_match is None or get_face_encoding is None:
        logger.error("Facial recognition backend unavailable: %s", _FACE_IMPORT_ERROR)
        _set_last_auth_error("Facial recognition backend is not installed.")
        return None

    if not _encodings_cache:
        _set_last_auth_error("Encodings cache is empty. Server may still be initialising.")
        return None

    # Validate login image has a detectable face
    login_encoding = get_face_encoding(login_image)
    if login_encoding is None or getattr(login_encoding, "shape", None) != (128,):
        _set_last_auth_error("No valid face detected in webcam capture.")
        return None

    # Find closest match against prebuilt encodings cache
    try:
        matched_uid = find_closest_match(login_image, _encodings_cache)
    except Exception as e:
        logger.error("Face recognition error: %s", e)
        _set_last_auth_error("No valid face detected or no matching profile found.")
        return None

    if not matched_uid:
        _set_last_auth_error("No matching identity found for scanned face.")
        return None

    # Confirm uid exists in MySQL
    try:
        mysql_connection = mysql.connector.connect(
            host=os.getenv("DB_HOST"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASS"),
            database=os.getenv("DB_NAME")
        )
        curr = mysql_connection.cursor()
        curr.execute("SELECT 1 FROM users WHERE uid = %s", (matched_uid,))
        if curr.fetchone() is None:
            curr.close()
            mysql_connection.close()
            logger.warning("UID %s not found in users table.", matched_uid)
            _set_last_auth_error("Matched UID not found in users table.")
            return None

        curr.execute("UPDATE users SET is_online = TRUE WHERE uid = %s", (matched_uid,))
        mysql_connection.commit()
        curr.close()
        mysql_connection.close()
    except MySQLError as e:
        logger.error("MySQL error: %s", e)
        _set_last_auth_error("Authentication backend unavailable (MySQL).")
        return None

    return matched_uid