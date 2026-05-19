"""Top-level S3 :class:`Client` wrapping a boto3 session."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, List, Optional, Union

import boto3
from botocore.exceptions import ClientError

from ._exceptions import (
    BucketAlreadyExistsError,
    BucketNotFoundError,
    S3Error,
)

if TYPE_CHECKING:
    from mypy_boto3_s3.client import S3Client
    from mypy_boto3_s3.service_resource import S3ServiceResource

    from ._bucket import Bucket


_DEFAULT_CLIENT: Optional[Client] = None


def get_default_client() -> Client:
    """Return a process-wide default :class:`Client`, creating it on first use."""
    global _DEFAULT_CLIENT
    if _DEFAULT_CLIENT is None:
        _DEFAULT_CLIENT = Client()
    return _DEFAULT_CLIENT


def reset_default_client() -> None:
    """Drop the cached default client. Used mainly in tests."""
    global _DEFAULT_CLIENT
    _DEFAULT_CLIENT = None


class Client:
    """Wraps a boto3 S3 client and resource with ezs3 conveniences.

    Credentials follow the usual boto3 resolution chain (env vars, shared config,
    instance role...). Pass any boto3 ``client``/``resource`` kwargs to override.
    """

    __slots__ = ("_boto_client", "_resource", "_session")

    def __init__(
        self,
        *,
        endpoint_url: Optional[str] = None,
        region_name: Optional[str] = None,
        aws_access_key_id: Optional[str] = None,
        aws_secret_access_key: Optional[str] = None,
        aws_session_token: Optional[str] = None,
        profile_name: Optional[str] = None,
        session: Optional[boto3.session.Session] = None,
        **boto3_kwargs: Any,
    ) -> None:
        if session is None:
            session = boto3.session.Session(
                aws_access_key_id=aws_access_key_id,
                aws_secret_access_key=aws_secret_access_key,
                aws_session_token=aws_session_token,
                region_name=region_name,
                profile_name=profile_name,
            )
        self._session = session

        client_kwargs: dict = dict(boto3_kwargs)
        if endpoint_url is not None:
            client_kwargs["endpoint_url"] = endpoint_url
        if region_name is not None:
            client_kwargs.setdefault("region_name", region_name)

        self._boto_client: S3Client = session.client("s3", **client_kwargs)
        self._resource: S3ServiceResource = session.resource("s3", **client_kwargs)

    @property
    def boto_client(self) -> S3Client:
        """Underlying ``boto3`` S3 client. Escape hatch for unsupported operations."""
        return self._boto_client

    @property
    def boto_resource(self) -> S3ServiceResource:
        """Underlying ``boto3`` S3 service resource."""
        return self._resource

    @property
    def region_name(self) -> Optional[str]:
        return self._boto_client.meta.region_name

    def __repr__(self) -> str:
        endpoint = self._boto_client.meta.endpoint_url
        return f"Client(endpoint_url={endpoint!r}, region={self.region_name!r})"

    # Bucket-level operations

    def bucket(self, name: Union[str, Bucket]) -> Bucket:
        """Return a :class:`Bucket` bound to this client.

        Does NOT verify that the bucket exists. Use :meth:`bucket_exists` for that.
        """
        from ._bucket import Bucket

        if isinstance(name, Bucket):
            return Bucket(name.name, client=self)
        return Bucket(name, client=self)

    def list_buckets(self) -> List[Bucket]:
        """List all buckets visible to the configured credentials."""
        from ._bucket import Bucket

        response = self._boto_client.list_buckets()
        return [Bucket(b["Name"], client=self) for b in response.get("Buckets") or []]

    def create_bucket(
        self,
        name: Union[str, Bucket],
        *,
        region: Optional[str] = None,
        exists_ok: bool = False,
        **extra: Any,
    ) -> Bucket:
        """Create a bucket and return its :class:`Bucket` handle."""
        from ._bucket import Bucket

        bucket_name = name.name if isinstance(name, Bucket) else name
        params: dict = {"Bucket": bucket_name, **extra}
        effective_region = region or self.region_name
        if effective_region and effective_region != "us-east-1":
            params.setdefault(
                "CreateBucketConfiguration",
                {"LocationConstraint": effective_region},
            )
        try:
            self._boto_client.create_bucket(**params)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
                if exists_ok:
                    return Bucket(bucket_name, client=self)
                raise BucketAlreadyExistsError(bucket_name) from exc
            raise S3Error(f"Failed to create bucket {bucket_name!r}: {exc}") from exc
        return Bucket(bucket_name, client=self)

    def delete_bucket(
        self,
        name: Union[str, Bucket],
        *,
        force: bool = False,
        missing_ok: bool = False,
    ) -> None:
        """Delete a bucket. With ``force=True`` empty it first."""
        from ._bucket import Bucket

        bucket = name if isinstance(name, Bucket) else Bucket(name, client=self)
        if force:
            bucket.clear()
        try:
            self._boto_client.delete_bucket(Bucket=bucket.name)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("NoSuchBucket", "404"):
                if missing_ok:
                    return
                raise BucketNotFoundError(bucket.name) from exc
            raise

    def bucket_exists(self, name: Union[str, Bucket]) -> bool:
        """Return whether the named bucket exists and is accessible."""
        from ._bucket import Bucket

        bucket_name = name.name if isinstance(name, Bucket) else name
        try:
            self._boto_client.head_bucket(Bucket=bucket_name)
            return True
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("404", "NoSuchBucket", "NotFound"):
                return False
            raise


__all__ = ["Client", "get_default_client", "reset_default_client"]
