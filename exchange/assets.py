"""Private image validation and storage. No remote URL fetching.

Images are stored by PRIVATE_STORAGE: "local" keeps them on this machine's disk; "s3" keeps them in a
private S3-compatible bucket, so separate web and worker services see the same files. Everything else
reads and writes images through the functions below, never through paths.
"""
import hashlib
import io
import uuid
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from PIL import Image, ImageOps, UnidentifiedImageError

Image.MAX_IMAGE_PIXELS = 16_800_000
MAX_BYTES = 9_500_000
# YouCam's minimum person photo is 512 x 384 pixels and its maximum side is 4096 pixels.
MIN_LONG_SIDE, MIN_SHORT_SIDE = 512, 384
MAX_SIDE = 4096
MAX_ASPECT_RATIO = 2.5


def normalize_photo(upload):
    if upload.size > MAX_BYTES:
        raise ValidationError("Image exceeds the 9.5 MB limit.")
    try:
        data = upload.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValidationError("Image exceeds the 9.5 MB limit.")
        image = Image.open(io.BytesIO(data))
        if image.format not in ("JPEG", "PNG"):
            raise ValidationError("Only JPEG or PNG images are accepted.")
        long_side, short_side = max(image.size), min(image.size)
        if long_side < MIN_LONG_SIDE or short_side < MIN_SHORT_SIDE:
            raise ValidationError("This photo is too small. Use one at least 512 by 384 pixels.")
        if long_side > MAX_SIDE:
            raise ValidationError("This photo is too large. Use one at most 4096 pixels on its longest side.")
        if long_side / short_side > MAX_ASPECT_RATIO:
            raise ValidationError("This photo is too narrow or too wide. Use an ordinary portrait or landscape photo.")
        image.load()
        # Phones often store rotation as metadata. Apply it, then re-encode, which drops all metadata.
        image = ImageOps.exif_transpose(image)
        out = io.BytesIO()
        image.convert("RGB").save(out, format="JPEG", quality=90, optimize=True)
        return out.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ValidationError("This file is not a usable JPEG or PNG image.") from None


class LocalStorage:
    """Files under PRIVATE_MEDIA_ROOT, readable by this machine only. Development, tests and one-machine hosts."""

    def save(self, key, data):
        root = Path(settings.PRIVATE_MEDIA_ROOT)
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = root / key
        with path.open("xb") as output:
            output.write(data)
        path.chmod(0o600)

    def read(self, key):
        return private_path(key).read_bytes()

    def exists(self, key):
        return private_path(key).is_file()

    def delete(self, key):
        private_path(key).unlink(missing_ok=True)


class S3Storage:
    """A private S3-compatible bucket, shared by the web and worker services. Nothing in it is public:
    images reach the browser only through the asset view, which checks the session first."""

    def __init__(self, client=None):
        self._client = client

    @property
    def client(self):
        if self._client is None:
            import boto3
            from botocore.config import Config
            self._client = boto3.client(
                "s3", endpoint_url=settings.PRIVATE_STORAGE_ENDPOINT, region_name=settings.PRIVATE_STORAGE_REGION,
                aws_access_key_id=settings.PRIVATE_STORAGE_ACCESS_KEY_ID,
                aws_secret_access_key=settings.PRIVATE_STORAGE_SECRET_ACCESS_KEY,
                config=Config(s3={"addressing_style": settings.PRIVATE_STORAGE_ADDRESSING},
                              connect_timeout=5, read_timeout=15, retries={"max_attempts": 3, "mode": "standard"}))
        return self._client

    def save(self, key, data):
        self.client.put_object(Bucket=settings.PRIVATE_STORAGE_BUCKET, Key=_checked(key), Body=data,
                               ContentType="image/jpeg")

    def read(self, key):
        from botocore.exceptions import ClientError
        try:
            response = self.client.get_object(Bucket=settings.PRIVATE_STORAGE_BUCKET, Key=_checked(key))
        except ClientError as exc:
            if _missing(exc):
                raise FileNotFoundError(key) from None
            raise
        return response["Body"].read()

    def exists(self, key):
        from botocore.exceptions import ClientError
        try:
            self.client.head_object(Bucket=settings.PRIVATE_STORAGE_BUCKET, Key=_checked(key))
        except ClientError as exc:
            if _missing(exc):
                return False
            raise
        return True

    def delete(self, key):
        # Deleting a key that is not there succeeds, as unlink(missing_ok=True) does.
        self.client.delete_object(Bucket=settings.PRIVATE_STORAGE_BUCKET, Key=_checked(key))


def _missing(exc):
    error = exc.response.get("Error", {})
    return str(error.get("Code")) in ("404", "NoSuchKey", "NotFound")


_storages = {}


def storage():
    """The backend PRIVATE_STORAGE names, made once per process."""
    name = settings.PRIVATE_STORAGE
    if name not in _storages:
        _storages[name] = {"local": LocalStorage, "s3": S3Storage}[name]()
    return _storages[name]


def save_private_image(data):
    key = uuid.uuid4().hex + ".jpg"
    storage().save(key, data)
    return key, hashlib.sha256(data).hexdigest()


def read_private_image(key):
    """The stored image's bytes. FileNotFoundError when it is gone."""
    return storage().read(_checked(key))


def private_image_exists(key):
    return storage().exists(_checked(key))


def delete_private_image(key):
    storage().delete(_checked(key))


def discard_asset(asset):
    """Delete the stored file first, then the record."""
    delete_private_image(asset.storage_key)
    asset.delete()


def _checked(key):
    if not key or Path(key).name != key or not key.endswith(".jpg"):
        raise ValueError("Invalid storage key.")
    return key


def private_path(key):
    """Where the local backend keeps an image. Only for local development, tests and the feasibility reports."""
    return Path(settings.PRIVATE_MEDIA_ROOT) / _checked(key)
