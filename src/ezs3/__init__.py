import os
from configparser import ConfigParser
from pathlib import Path

import boto3
import botocore
from dictdot import dictdot


class S3:
    DEFAULT_CREDENTIALS_FILE = Path.home().joinpath(".aws/credentials").as_posix()

    @classmethod
    def from_credentials(
        cls,
        default_bucket,
        credentials_file=DEFAULT_CREDENTIALS_FILE,
        profile="default",
    ):
        cfg = ConfigParser()
        if cfg.read(credentials_file):
            creds = dict(cfg).get(profile)
            if creds:
                creds = {
                    "aws_access_key_id": creds.get("aws_access_key_id"),
                    "aws_secret_access_key": creds.get("aws_secret_access_key"),
                    "aws_session_token": creds.get("aws_session_token"),
                }
                return cls(default_bucket, **creds)
            else:
                raise ValueError(f"Can't find {profile=} in {credentials_file=}")
        else:
            raise ValueError(f"Can't parse {credentials_file=}")

    def __init__(self, default_bucket, base_path=".", **kwargs):
        """
        When calling methods that manipulate paths, they will be relative to
        `base_path`. You can use absolute paths as well.
        """
        self.base_path = Path(base_path).resolve()
        self.base_path.mkdir(parents=True, exist_ok=True)

        self._resource = boto3.resource("s3", **kwargs)
        try:
            buckets = {b.name: b for b in self._resource.buckets.all()}
            self.buckets = dictdot(buckets)
        except botocore.exceptions.ClientError as e:
            not_allowed = e.response["Error"]["Code"] == "AccessDenied"
            if default_bucket is None and not_allowed:
                raise ValueError("Can't list all buckets. Provide a `default_bucket`.") from e
            bucket = self._resource.Bucket(default_bucket)
            self.buckets = dictdot({default_bucket: bucket})

        self.bucket = self.buckets.get(default_bucket)
        # TODO add checks using head_bucket()

        # method aliases.
        self.rm = self.remove
        self.ls = self.list_keys

    def set_default_bucket(self, bucket_name):
        self.bucket = self.buckets[bucket_name]

    # methods to handle files and dirs.

    def is_dir(self, *paths):
        """Check if joining the arguments results in a directory name."""
        last_path = str(paths[-1]) if paths else ""
        return last_path.endswith("/") or self.base_path.joinpath(*paths).is_dir()

    def get_abspath(self, *paths, create_dirs=False):
        """Build absolute path joining positional args with `self.base_path`.

        If `create_dirs` then it will create the required directories to ensure
        read/write operations in the resulting path.
        """
        abs_path = self.base_path.joinpath(*paths)
        if create_dirs:
            if self.is_dir(*paths):
                abs_path.mkdir(parents=True, exist_ok=True)
            else:
                abs_path.parent.mkdir(parents=True, exist_ok=True)
        return abs_path

    # methods to handle S3 objects.

    def find_keys(self, s3_prefix="", remove_prefix=False):
        """Generator that yields keys under `s3_prefix`."""
        if s3_prefix and not s3_prefix.endswith("/"):
            s3_prefix += "/"
        for obj in self.bucket.objects.filter(Prefix=s3_prefix):
            s3_key = obj.key
            if remove_prefix:
                s3_key = s3_key[len(s3_prefix) :]
            yield s3_key

    def list_keys(self, s3_prefix="", remove_prefix=False):
        """List keys under `s3_prefix`."""
        return list(self.find_keys(s3_prefix, remove_prefix))

    def _upload_single_file(self, abs_local_path, s3_key):
        if s3_key.endswith("/"):
            # `s3_key` is not a valid key. append filename.
            s3_key += abs_local_path.name
        # self.log(f"Uploading {abs_local_path} to {s3_key}")
        self.bucket.upload_file(
            Filename=abs_local_path.as_posix(),
            Key=s3_key,
        )

    def upload(self, local_path, s3_prefix):
        """Upload files to S3, recursively if `local_path` is a directory."""
        abs_local_path = self.get_abspath(local_path)
        if not abs_local_path.exists():
            raise FileNotFoundError(abs_local_path)

        if abs_local_path.is_file():
            self._upload_single_file(abs_local_path, s3_prefix)
        elif abs_local_path.is_dir():
            # recursively build destination `s3_prefix` from directory structure.
            for subpath in abs_local_path.iterdir():
                self.upload(subpath, os.path.join(s3_prefix, subpath.name))
        else:
            raise ValueError(f"Invalid path: {abs_local_path}")

    def _download_single_file(self, s3_key, abs_local_path, overwrite):
        if abs_local_path.is_dir():
            # append destination filename to path.
            s3_filename = os.path.basename(s3_key)
            abs_local_path = abs_local_path.joinpath(s3_filename)

        if not abs_local_path.exists() or overwrite:
            with open(abs_local_path, "wb") as f:
                # self.log(f"Downloading {s3_key} into {abs_local_path}")
                self.bucket.download_fileobj(
                    Key=s3_key,
                    Fileobj=f,
                )
        else:
            # self.log(f"Skipping download of {s3_key} into {abs_local_path}")
            pass

    def download(self, s3_source, local_path, overwrite=False):
        """Download files from S3, recursively if `s3_source` is a prefix."""
        try:
            # check if `s3_source` is a valid S3 key, and download it.
            self.bucket.Object(s3_source).load()
            abs_local_path = self.get_abspath(local_path, create_dirs=True)
            self._download_single_file(s3_source, abs_local_path, overwrite)

        except botocore.exceptions.ClientError as e:
            if e.response["Error"]["Code"] == "404":
                # else, try to download keys under `s3_source`.
                keys = self.list_keys(s3_source, remove_prefix=True)
                if not keys:
                    raise FileNotFoundError(f"Nothing to download from {s3_source}.") from e

                for s3_key in keys:
                    abs_local_path = self.get_abspath(
                        local_path,
                        s3_key,
                        create_dirs=True,
                    )
                    self._download_single_file(
                        os.path.join(s3_source, s3_key),
                        abs_local_path,
                        overwrite,
                    )
            else:
                raise FileNotFoundError(f"Nothing to download from {s3_source}.") from e

    def remove(self, *s3_keys):
        self.bucket.delete_objects(Delete={"Objects": [{"Key": k} for k in s3_keys]})
