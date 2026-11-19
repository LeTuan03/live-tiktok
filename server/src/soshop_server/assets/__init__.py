"""S3 / S3-compatible asset hosting for product images and avatar uploads.

Public surface:

* :func:`upload_bytes` — server-side upload of an in-memory blob, returns the
  CDN-friendly public URL.
* :func:`generate_presigned_put_url` — issue a short-lived signed URL so the
  client can ``PUT`` directly to the bucket.
* :func:`public_url_for_key` — turn a stored object key into the public URL
  that should be returned to clients (uses ``S3_PUBLIC_URL``).
"""

from soshop_server.assets.service import (
    AssetsNotConfiguredError,
    PresignedUpload,
    build_object_key,
    generate_presigned_put_url,
    public_url_for_key,
    upload_bytes,
)

__all__ = [
    "AssetsNotConfiguredError",
    "PresignedUpload",
    "build_object_key",
    "generate_presigned_put_url",
    "public_url_for_key",
    "upload_bytes",
]
