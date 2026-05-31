"""Top-level :class:`Client` wrapping a boto3 session.

The :class:`Client` is the entry point for credential resolution and
bucket-level lifecycle (list / create / delete). It mirrors the high-level
methods exposed by ``boto3.client("s3")`` while replacing dict-based responses
with typed :class:`~ezs3.Bucket` handles.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING, List, Optional, Tuple, Union, cast

import boto3
from botocore.exceptions import ClientError

from ._exceptions import (
    BucketAlreadyExistsError,
    BucketNotFoundError,
    S3Error,
)

if TYPE_CHECKING:
    from botocore.config import Config
    from mypy_boto3_s3.client import S3Client
    from mypy_boto3_s3.literals import BucketLocationConstraintType
    from mypy_boto3_s3.service_resource import S3ServiceResource
    from mypy_boto3_s3.type_defs import (
        CreateBucketRequestBucketCreateTypeDef as CreateBucketKwargs,
    )
    from mypy_boto3_s3.type_defs import CreateBucketRequestTypeDef
    from typing_extensions import Unpack

    from ._bucket import Bucket


@lru_cache(maxsize=None)
def get_default_client() -> Client:
    """Return the process-wide default :class:`Client`.

    The client is created on first access using the standard boto3 credential
    chain (environment variables, shared config, instance role...).

    Returns:
        The cached default :class:`Client` instance.
    """
    return Client()


def reset_default_client() -> None:
    """Drop the cached default client.

    Mainly useful in tests that swap out credentials or wrap calls in a
    ``moto`` mock between cases.
    """
    get_default_client.cache_clear()


class Client:
    """Wraps a boto3 S3 client and resource with ezs3 conveniences.

    Credentials follow the standard boto3 resolution chain (environment
    variables, shared config, instance role...). Any kwargs not explicitly
    listed below are forwarded to ``boto3.Session.client``/``.resource``.

    Example:
        >>> import ezs3
        >>> client = ezs3.Client(region_name="eu-west-1")
        >>> bucket = client.create_bucket("my-bucket")
        >>> bucket.write_text("hello.txt", "hi")
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
        config: Optional[Config] = None,
        api_version: Optional[str] = None,
        use_ssl: Optional[bool] = None,
        verify: Optional[Union[bool, str]] = None,
    ) -> None:
        """Initialize a new :class:`Client`.

        Args:
            endpoint_url: Custom S3 endpoint. Useful for local development
                against MinIO, LocalStack, or other S3-compatible services.
            region_name: AWS region used for the underlying session.
            aws_access_key_id: Explicit access key. Falls back to the boto3
                credential chain when omitted.
            aws_secret_access_key: Explicit secret key. See ``aws_access_key_id``.
            aws_session_token: Optional STS session token.
            profile_name: Named profile from ``~/.aws/credentials``.
            session: Pre-built ``boto3.session.Session``. When provided, the
                credential/region kwargs above are ignored.
            config: Optional ``botocore.config.Config`` forwarded to both
                ``session.client("s3", ...)`` and ``session.resource("s3", ...)``.
            api_version: Pin a specific S3 API version. Defaults to the
                latest known to the installed ``botocore``.
            use_ssl: Whether to use TLS when talking to the endpoint.
                Defaults to ``True`` inside boto3.
            verify: TLS verification flag. ``True``/``False`` toggles
                verification; a string is interpreted as a path to a CA
                bundle.
        """
        if session is None:
            session = boto3.session.Session(
                aws_access_key_id=aws_access_key_id,
                aws_secret_access_key=aws_secret_access_key,
                aws_session_token=aws_session_token,
                region_name=region_name,
                profile_name=profile_name,
            )
        self._session = session

        self._boto_client: S3Client = session.client(
            "s3",
            region_name=region_name,
            api_version=api_version,
            use_ssl=use_ssl if use_ssl is not None else True,
            verify=verify,
            endpoint_url=endpoint_url,
            config=config,
        )
        self._resource: S3ServiceResource = session.resource(
            "s3",
            region_name=region_name,
            api_version=api_version,
            use_ssl=use_ssl if use_ssl is not None else True,
            verify=verify,
            endpoint_url=endpoint_url,
            config=config,
        )

    @property
    def boto_client(self) -> S3Client:
        """The underlying ``boto3`` S3 client.

        Escape hatch for operations not surfaced by ezs3.
        """
        return self._boto_client

    @property
    def boto_resource(self) -> S3ServiceResource:
        """The underlying ``boto3`` S3 service resource."""
        return self._resource

    @property
    def region_name(self) -> Optional[str]:
        """The AWS region this client targets, or ``None`` if unset."""
        return self._boto_client.meta.region_name

    def __repr__(self) -> str:
        endpoint = self._boto_client.meta.endpoint_url
        return f"Client(endpoint_url={endpoint!r}, region={self.region_name!r})"

    def _credential_key(self) -> Optional[Tuple[Optional[str], Optional[str], Optional[str]]]:
        """Return a tuple identifying the currently-resolved credentials.

        The tuple is ``(access_key, secret_key, session_token)`` taken from a
        frozen snapshot of the session's credentials, or ``None`` when the
        session has no credentials (anonymous access). Two clients backed by
        different credentials -- even on the same AWS account -- produce
        different keys, because each credential set may carry distinct IAM
        permissions and must therefore be treated as a distinct identity.
        """
        creds = self._session.get_credentials()
        if creds is None:
            return None
        frozen = creds.get_frozen_credentials()
        return (frozen.access_key, frozen.secret_key, frozen.token)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Client):
            return NotImplemented
        return self._credential_key() == other._credential_key()

    def __hash__(self) -> int:
        key = self._credential_key()
        access_key = key[0] if key is not None else None
        return hash(("ezs3.Client", access_key))

    # Bucket-level operations

    def bucket(self, name: Union[str, Bucket]) -> Bucket:
        """Return a :class:`~ezs3.Bucket` handle bound to this client.

        The handle is purely local; the bucket itself is **not** verified to
        exist. Use :meth:`bucket_exists` for that.

        Args:
            name: Bucket name, or an existing :class:`~ezs3.Bucket` whose name
                will be re-bound to this client.

        Returns:
            A new :class:`~ezs3.Bucket` bound to this client.
        """
        from ._bucket import Bucket

        if isinstance(name, Bucket):
            return Bucket(name.name, client=self)
        return Bucket(name, client=self)

    def list_buckets(self) -> List[Bucket]:
        """List all buckets visible to the configured credentials.

        Returns:
            One :class:`~ezs3.Bucket` per bucket returned by the S3 API,
            preserving the API's ordering.
        """
        from ._bucket import Bucket

        response = self._boto_client.list_buckets()
        return [Bucket(b["Name"], client=self) for b in response.get("Buckets") or []]

    def create_bucket(
        self,
        name: Union[str, Bucket],
        *,
        region: Optional[str] = None,
        exists_ok: bool = False,
        **extra: Unpack[CreateBucketKwargs],
    ) -> Bucket:
        """Create a bucket and return its :class:`~ezs3.Bucket` handle.

        For any region other than ``us-east-1`` a ``CreateBucketConfiguration``
        is automatically added, since the S3 API requires it.

        Args:
            name: Bucket name, or an existing :class:`~ezs3.Bucket` to reuse
                the name from.
            region: Region to create the bucket in. Defaults to the client's
                configured region.
            exists_ok: When ``True``, suppress
                :class:`~ezs3.BucketAlreadyExistsError` and return a handle to
                the pre-existing bucket.
            **extra: Additional kwargs forwarded to the underlying
                ``boto_client.create_bucket`` call. Statically typed by
                ``mypy_boto3_s3.type_defs.CreateBucketRequestBucketCreateTypeDef``
                -- the resource-method variant that omits ``Bucket``
                (filled from ``name``).

        Returns:
            A :class:`~ezs3.Bucket` handle pointing at the newly created (or
            pre-existing, if ``exists_ok``) bucket.

        Raises:
            BucketAlreadyExistsError: When the bucket already exists and
                ``exists_ok`` is ``False``.
            S3Error: For any other failure surfaced by the S3 API.
        """
        from ._bucket import Bucket

        bucket_name = name.name if isinstance(name, Bucket) else name
        params: CreateBucketRequestTypeDef = {"Bucket": bucket_name, **extra}
        effective_region = region or self.region_name
        if effective_region and effective_region != "us-east-1":
            params.setdefault(
                "CreateBucketConfiguration",
                {
                    "LocationConstraint": cast(
                        "BucketLocationConstraintType",
                        effective_region,
                    ),
                },
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
        """Delete a bucket.

        Args:
            name: Bucket name or :class:`~ezs3.Bucket` handle.
            force: When ``True``, empty the bucket first by deleting every
                object under it. Without this flag, a non-empty bucket will
                cause the S3 API to refuse the delete.
            missing_ok: When ``True``, suppress
                :class:`~ezs3.BucketNotFoundError`.

        Raises:
            BucketNotFoundError: When the bucket does not exist and
                ``missing_ok`` is ``False``.
        """
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
        """Check whether a bucket exists and is accessible to this client.

        Args:
            name: Bucket name or :class:`~ezs3.Bucket` handle.

        Returns:
            ``True`` if the bucket exists and the configured credentials may
            ``HEAD`` it, ``False`` otherwise.
        """
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
