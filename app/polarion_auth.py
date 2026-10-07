"""Polarion-issued tokens: who a merge job is made for.

The PDF Exporter runs inside Polarion and asks Polarion for a short-lived JWT for every call it makes
(``X-Polarion-Token``). Polarion signs it with a key whose public half it publishes as a JWKS, so this service needs
no secret shared with its client: it fetches that key set, checks the signature and trusts what is inside.

* ``sub`` is the Polarion user the merge is made for. The service keeps a digest of it as the initiator of the job,
  and only a token of that user is accepted for the job afterwards.
* ``job`` names the job a token is for. ``start`` has no job yet and carries none, every later call carries the ID it
  addresses, so a token cannot be used for another job.
* ``svc`` names this service, so a token made for another one is of no use here.
* ``exp`` and ``iat`` bound the life of a token. A token which lives longer than ``POLARION_TOKEN_MAX_AGE`` is refused.

The check is switched on by ``POLARION_JWKS_URL``. Without it the service behaves as before. Where it is on, a call
without a valid token is answered with 401 - or 503 where the key set cannot be fetched, since nothing can be verified
then - and a valid token which is not the one of the job's initiator with 404, exactly as for a job which does not
exist, so the answer does not tell which job IDs are in use.

Polarion answers a request for its keys only under the host name of its own base URL. Where the service reaches it by another
name, ``POLARION_JWKS_HOST`` says which one to ask under.

The key behind a ``kid`` can change: Polarion makes a new one when it restarts. A signature which does not verify is
therefore checked once more against a fresh key set before it is refused.
"""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any

import jwt
from fastapi import Header, HTTPException, Request, status
from jwt import PyJWKClient

from app.constants import POLARION_JWKS_HOST, POLARION_JWKS_URL, POLARION_TOKEN_MAX_AGE
from app.weasyprint_client import TLS_CONTEXT

if TYPE_CHECKING:
    from app.models import JobMetadata

logger = logging.getLogger(__name__)

# the name of a header, not a secret
POLARION_TOKEN_HEADER = "X-Polarion-Token"  # noqa: S105
SERVICE_NAME = "bulk-processing-service"
SERVICE_CLAIM = "svc"
JOB_CLAIM = "job"

# Only what Polarion signs its tokens with. A token names its own algorithm, and honouring that is how a token
# signed with "none", or with a public key used as a shared secret, gets accepted.
_ALGORITHMS = ["RS256"]
_LEEWAY_SECONDS = 30
# a host name, or an address, with a port where there is one: what a Host header holds, and nothing a header could be
# broken out of with
_HOST_PATTERN = re.compile(r"[A-Za-z\d.\-]+(:\d{1,5})?|\[[\dA-Fa-f:.]+\](:\d{1,5})?", re.ASCII)
_JWKS_CACHE_SECONDS = 300
_JWKS_TIMEOUT_SECONDS = 5
# a forced re-fetch of the key set (replaced key behind a known kid) is allowed this rarely,
# so forged tokens cannot turn every call into a request to Polarion
_FORCED_REFRESH_INTERVAL_SECONDS = 30


# The members of an RSA key which belong to its private half. A public key set has none of them.
_PRIVATE_MEMBERS = frozenset({"d", "p", "q", "dp", "dq", "qi", "oth"})


class PolarionJwkClient(PyJWKClient):
    """
    A client for the key set Polarion publishes, which is not a clean public key set.

    Polarion's keys carry a private member, ``d``, as JSON ``null``. PyJWT takes the mere presence of ``d`` for a private
    key, cannot build one from ``null``, and refuses the whole set ("did not contain any usable keys"), so every token of
    Polarion would be refused, a valid one too. The private members are dropped before the set is read: a verifier needs
    the public half only, and a key which does carry a real private half is not made any more usable by it.

    The library reads the set while it fetches it, before ``fetch_data`` returns, so the set has to be cleaned where the
    library checks the payload: ``_as_jwk_set_payload`` is the hook it gives a subclass for that. The version of PyJWT is
    pinned, and the tests go through a real fetch, so a release which moves the hook is not taken unnoticed.
    """

    @staticmethod
    def _as_jwk_set_payload(data: Any) -> dict[str, Any]:
        payload = PyJWKClient._as_jwk_set_payload(data)
        keys = payload.get("keys")
        if not isinstance(keys, list):
            return payload
        return {**payload, "keys": [{field: value for field, value in key.items() if field not in _PRIVATE_MEMBERS} if isinstance(key, dict) else key for key in keys]}


class TokenRejectedError(Exception):
    """The token is missing, malformed, expired, forged or not made for this service."""


class JwksUnavailableError(Exception):
    """The key set of Polarion cannot be fetched, so no token can be verified."""


@dataclass(frozen=True)
class Principal:
    """What a verified token says: the user as a digest, and the job it is for, if any."""

    user_hash: str
    job: str | None


def hash_user(user: str) -> str:
    return hashlib.sha256(user.encode("utf-8")).hexdigest()


class PolarionTokenVerifier:
    def __init__(self, jwks_client: PyJWKClient, max_age_seconds: int) -> None:
        self._jwks_client = jwks_client
        self._max_age_seconds = max_age_seconds
        self._refresh_lock = threading.Lock()
        self._last_forced_refresh = float("-inf")

    def verify(self, token: str) -> Principal:
        """
        Check a token and return what it says.

        Raises:
            TokenRejectedError: when the token is not acceptable.
            JwksUnavailableError: when the key set of Polarion cannot be fetched.
        """
        try:
            claims = self._decode(token)
        except jwt.PyJWTError as e:
            raise TokenRejectedError(type(e).__name__) from e

        if claims.get(SERVICE_CLAIM) != SERVICE_NAME:
            raise TokenRejectedError("not made for this service")
        user = claims.get("sub")
        if not isinstance(user, str) or not user.strip():
            raise TokenRejectedError("no user")
        if claims["exp"] - claims["iat"] > self._max_age_seconds:
            raise TokenRejectedError("lives too long")
        job = claims.get(JOB_CLAIM)
        if job is not None and not isinstance(job, str):
            raise TokenRejectedError("bad job")
        return Principal(user_hash=hash_user(user), job=job)

    def _decode(self, token: str) -> dict[str, Any]:
        try:
            return self._decode_once(token)
        except jwt.InvalidSignatureError:
            # the key behind this kid may have been replaced since the set was cached
            if not self._refresh():
                raise
            return self._decode_once(token)

    def _decode_once(self, token: str) -> dict[str, Any]:
        try:
            key = self._jwks_client.get_signing_key_from_jwt(token)
        except (jwt.PyJWKClientConnectionError, ValueError) as e:
            raise JwksUnavailableError(type(e).__name__) from e
        # an unknown kid is looked up once more in a fresh set by the client itself
        return jwt.decode(  # type: ignore[no-any-return]
            token,
            key.key,
            algorithms=_ALGORITHMS,
            options={"require": ["exp", "iat", "sub"]},
            leeway=_LEEWAY_SECONDS,
        )

    def _refresh(self) -> bool:
        """Force a re-fetch of the key set unless one was forced recently. Returns whether it was done."""
        with self._refresh_lock:
            now = time.monotonic()
            if now - self._last_forced_refresh < _FORCED_REFRESH_INTERVAL_SECONDS:
                return False
            self._last_forced_refresh = now
        try:
            self._jwks_client.get_jwk_set(refresh=True)
        except (jwt.PyJWKClientConnectionError, jwt.PyJWKSetError, ValueError) as e:
            raise JwksUnavailableError(type(e).__name__) from e
        return True


def jwks_headers(host: str | None) -> dict[str, str] | None:
    """The headers to ask Polarion for its key set with: the host name it is to be asked under, where one is named.

    Raises:
        ValueError: when the name is not a host name, which stops the service from starting rather than lets it send a
            header which is something else.
    """
    if host is None:
        return None
    if not _HOST_PATTERN.fullmatch(host):
        msg = "POLARION_JWKS_HOST has to be a host name or an address, with a port where there is one"
        raise ValueError(msg)
    return {"Host": host}


def build_verifier() -> PolarionTokenVerifier | None:
    """The verifier for the configured key set, or None where the check is not switched on."""
    if not POLARION_JWKS_URL:
        return None
    # cache_keys stays off on purpose: it would keep the parsed key of a kid for good, and a refetched key set could not
    # replace it. The key set itself is cached (lifespan), picking a key out of it costs nothing.
    client = PolarionJwkClient(
        POLARION_JWKS_URL,
        cache_keys=False,
        lifespan=_JWKS_CACHE_SECONDS,
        timeout=_JWKS_TIMEOUT_SECONDS,
        ssl_context=TLS_CONTEXT,
        headers=jwks_headers(POLARION_JWKS_HOST),
    )
    return PolarionTokenVerifier(client, POLARION_TOKEN_MAX_AGE)


def require_principal(
    request: Request,
    x_polarion_token: Annotated[
        str | None,
        Header(alias=POLARION_TOKEN_HEADER, description="Token Polarion issued for this call. Required when POLARION_JWKS_URL is set."),
    ] = None,
) -> Principal | None:
    """
    Verify the token of the request.

    Returns None while the check is not switched on, which leaves every endpoint as it was.

    Raises:
        HTTPException: 401 when the token is missing or not acceptable, 503 when the key set of Polarion is unreachable.
    """
    verifier: PolarionTokenVerifier | None = getattr(request.app.state, "polarion_verifier", None)
    if verifier is None:
        return None

    token = (x_polarion_token or "").strip()
    if not token:
        logger.warning("Rejected request to %s: no Polarion token", request.url.path)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Polarion token")
    try:
        return verifier.verify(token)
    except TokenRejectedError as e:
        # never the token itself, only why it was refused
        logger.warning("Rejected request to %s: Polarion token refused (%s)", request.url.path, e)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Polarion token") from e
    except JwksUnavailableError as e:
        logger.exception("Cannot verify the Polarion token of a request to %s: key set unreachable", request.url.path)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Cannot verify the Polarion token: its key set is unreachable") from e


def may_use_job(principal: Principal | None, job_id: str, metadata: JobMetadata) -> bool:
    """
    Whether the verified token may be used for the job.

    With no principal the check is not switched on and everything is allowed. Otherwise the token has to be made for
    this very job, by the user the job was started for. A job which has no initiator - started before the check was
    switched on - is closed to everybody, since nobody can prove they started it.
    """
    if principal is None:
        return True
    if principal.job != job_id or metadata.initiator_hash is None:
        return False
    return secrets.compare_digest(metadata.initiator_hash.encode(), principal.user_hash.encode())
