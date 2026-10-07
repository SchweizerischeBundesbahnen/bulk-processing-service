"""Tests for the Polarion tokens which say who a merge job is made for."""

import base64
import contextlib
import hashlib
import hmac
import io
import json
import time
from unittest.mock import MagicMock, patch

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt import PyJWKClient

from app import polarion_auth
from app.app import app
from app.job_manager import JobManager
from app.polarion_auth import JOB_CLAIM, PolarionJwkClient, POLARION_TOKEN_HEADER, SERVICE_CLAIM, SERVICE_NAME, JwksUnavailableError, PolarionTokenVerifier, hash_user

from .test_app import SAMPLE_PDF

KID = "standalone"
JOBS = "/api/convert"


def _new_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def key() -> rsa.RSAPrivateKey:
    return _new_key()


@pytest.fixture(scope="module")
def other_key() -> rsa.RSAPrivateKey:
    return _new_key()


class FakeJwksClient(PyJWKClient):
    """A PyJWKClient whose key set is whatever the test says, and which counts the times it was asked for it."""

    def __init__(self, private_key: rsa.RSAPrivateKey | None) -> None:
        super().__init__("http://polarion.invalid/jwks.json", cache_keys=False, lifespan=300)
        self.fetches = 0
        self.unreachable = False
        self.key = private_key

    def publish(self, private_key: rsa.RSAPrivateKey) -> None:
        self.key = private_key

    def fetch_data(self) -> dict:
        self.fetches += 1
        if self.unreachable:
            raise jwt.PyJWKClientConnectionError("down")
        jwk = jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key(), as_dict=True)
        return {"keys": [{**jwk, "kid": KID, "alg": "RS256", "use": "sig"}]}


def make_token(key: rsa.RSAPrivateKey, user: str = "alice", job: str | None = None, *, svc: str | None = SERVICE_NAME, kid: str = KID, issued: float | None = None,
               lifetime: float = 300, **extra) -> str:
    now = time.time() if issued is None else issued
    claims = {"sub": user, "iat": int(now), "exp": int(now + lifetime), **extra}
    if svc is not None:
        claims[SERVICE_CLAIM] = svc
    if job is not None:
        claims[JOB_CLAIM] = job
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})


@pytest.fixture
def jwks(key) -> FakeJwksClient:
    return FakeJwksClient(key)


@pytest.fixture
def client(tmp_path, jwks):
    app.state.job_manager = JobManager(tmp_path / "jobs")
    app.state.polarion_verifier = PolarionTokenVerifier(jwks, 900)
    yield TestClient(app, raise_server_exceptions=True)
    app.state.polarion_verifier = None


def auth(token: str) -> dict[str, str]:
    return {POLARION_TOKEN_HEADER: token}


def start(client, key, user: str = "alice") -> str:
    response = client.post(f"{JOBS}/start", json={}, headers=auth(make_token(key, user)))
    assert response.status_code == 201
    return response.json()["jobId"]


def _mock_weasyprint(mock_get_client):
    mock = MagicMock()
    mock.convert_html_to_pdf.return_value = SAMPLE_PDF
    mock_get_client.return_value = mock


class TestSwitchedOff:
    def test_no_verifier_is_no_check(self, tmp_path):
        app.state.job_manager = JobManager(tmp_path / "jobs")
        app.state.polarion_verifier = None
        client = TestClient(app, raise_server_exceptions=True)
        job_id = client.post(f"{JOBS}/start", json={}).json()["jobId"]
        assert client.delete(f"{JOBS}/{job_id}").status_code == 204

    def test_unset_url_builds_no_verifier(self):
        with patch.object(polarion_auth, "POLARION_JWKS_URL", None):
            assert polarion_auth.build_verifier() is None

    def test_set_url_builds_a_verifier(self):
        with patch.object(polarion_auth, "POLARION_JWKS_URL", "https://polarion/polarion/.well-known/jwks.json"):
            assert isinstance(polarion_auth.build_verifier(), PolarionTokenVerifier)


class TestStart:
    def test_valid_token_starts_a_job_for_its_user(self, client, key):
        job_id = start(client, key, "alice")
        assert app.state.job_manager.get_job_metadata(job_id).initiator_hash == hash_user("alice")

    def test_the_user_name_is_not_stored(self, client, key):
        job_id = start(client, key, "alice")
        assert "alice" not in (app.state.job_manager.storage_dir / job_id / "metadata.json").read_text()

    def test_missing_token_is_401(self, client):
        assert client.post(f"{JOBS}/start", json={}).status_code == 401

    @pytest.mark.parametrize("token", ["", "   ", "garbage", "a.b.c"])
    def test_malformed_token_is_401(self, client, token):
        assert client.post(f"{JOBS}/start", json={}, headers=auth(token)).status_code == 401

    def test_token_signed_with_another_key_is_401(self, client, other_key):
        assert client.post(f"{JOBS}/start", json={}, headers=auth(make_token(other_key))).status_code == 401

    def test_expired_token_is_401(self, client, key):
        token = make_token(key, issued=time.time() - 3600, lifetime=300)
        assert client.post(f"{JOBS}/start", json={}, headers=auth(token)).status_code == 401

    def test_token_made_for_another_service_is_401(self, client, key):
        assert client.post(f"{JOBS}/start", json={}, headers=auth(make_token(key, svc="another-service"))).status_code == 401
        assert client.post(f"{JOBS}/start", json={}, headers=auth(make_token(key, svc=None))).status_code == 401

    def test_token_which_lives_too_long_is_401(self, client, key):
        assert client.post(f"{JOBS}/start", json={}, headers=auth(make_token(key, lifetime=3600))).status_code == 401

    @pytest.mark.parametrize("missing", ["exp", "iat", "sub"])
    def test_token_without_a_required_claim_is_401(self, client, key, missing):
        claims = {"sub": "alice", "iat": int(time.time()), "exp": int(time.time()) + 300, SERVICE_CLAIM: SERVICE_NAME}
        del claims[missing]
        token = jwt.encode(claims, key, algorithm="RS256", headers={"kid": KID})
        assert client.post(f"{JOBS}/start", json={}, headers=auth(token)).status_code == 401

    def test_token_without_a_user_is_401(self, client, key):
        assert client.post(f"{JOBS}/start", json={}, headers=auth(make_token(key, user="  "))).status_code == 401

    def test_unsigned_token_is_401(self, client):
        claims = {"sub": "alice", "iat": int(time.time()), "exp": int(time.time()) + 300, SERVICE_CLAIM: SERVICE_NAME}
        token = jwt.encode(claims, None, algorithm="none", headers={"kid": KID})
        assert client.post(f"{JOBS}/start", json={}, headers=auth(token)).status_code == 401

    def test_public_key_used_as_a_shared_secret_is_401(self, client, key):
        # the classic algorithm confusion: HS256 with the public key as the secret. PyJWT refuses to build such a token,
        # so it is built by hand, the way an attacker would.
        public_pem = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        header = {"alg": "HS256", "typ": "JWT", "kid": KID}
        claims = {"sub": "alice", "iat": int(time.time()), "exp": int(time.time()) + 300, SERVICE_CLAIM: SERVICE_NAME}
        signing_input = ".".join(base64.urlsafe_b64encode(json.dumps(part).encode()).rstrip(b"=").decode() for part in (header, claims))
        signature = base64.urlsafe_b64encode(hmac.new(public_pem, signing_input.encode(), hashlib.sha256).digest()).rstrip(b"=").decode()
        assert client.post(f"{JOBS}/start", json={}, headers=auth(f"{signing_input}.{signature}")).status_code == 401

    def test_unknown_key_id_is_401(self, client, key):
        assert client.post(f"{JOBS}/start", json={}, headers=auth(make_token(key, kid="nobody"))).status_code == 401

    def test_the_token_never_reaches_the_answer_or_the_log(self, client, other_key, caplog):
        token = make_token(other_key)
        with caplog.at_level("DEBUG"):
            response = client.post(f"{JOBS}/start", json={}, headers=auth(token))
        assert token not in response.text
        assert token not in caplog.text


class TestJobBinding:
    @patch("app.converter_controller.get_weasyprint_client")
    def test_initiator_can_use_the_job_with_a_token_made_for_it(self, mock_get_client, client, key):
        _mock_weasyprint(mock_get_client)
        job_id = start(client, key)
        token = make_token(key, job=job_id)
        assert client.post(f"{JOBS}/{job_id}/add", json={"html": "<p>x</p>"}, headers=auth(token)).status_code == 202
        assert client.post(f"{JOBS}/{job_id}/finish", headers=auth(token)).status_code == 200
        assert client.delete(f"{JOBS}/{job_id}", headers=auth(token)).status_code == 204

    @patch("app.converter_controller.get_weasyprint_client")
    def test_other_user_gets_404_on_every_endpoint(self, mock_get_client, client, key):
        _mock_weasyprint(mock_get_client)
        job_id = start(client, key, "alice")
        bob = auth(make_token(key, "bob", job=job_id))
        assert client.post(f"{JOBS}/{job_id}/add", json={"html": "<p>x</p>"}, headers=bob).status_code == 404
        assert client.post(f"{JOBS}/{job_id}/finish", headers=bob).status_code == 404
        assert client.delete(f"{JOBS}/{job_id}", headers=bob).status_code == 404
        mock_get_client.assert_not_called()
        # the job is still there, untouched by the refused calls
        assert app.state.job_manager.get_job_metadata(job_id) is not None

    @patch("app.converter_controller.get_weasyprint_client")
    def test_add_with_attachments_is_bound_to_the_initiator_too(self, mock_get_client, client, key):
        _mock_weasyprint(mock_get_client)
        job_id = start(client, key, "alice")
        url = f"{JOBS}/{job_id}/add-with-attachments"
        data = {"html": "<p>x</p>"}
        # refused before the form is read, and before anything is converted
        assert client.post(url, data=data, headers=auth(make_token(key, "bob", job=job_id))).status_code == 404
        assert client.post(url, data=data).status_code == 401
        mock_get_client.assert_not_called()
        assert client.post(url, data=data, headers=auth(make_token(key, "alice", job=job_id))).status_code == 202

    def test_token_made_for_another_job_opens_nothing(self, client, key):
        first, second = start(client, key), start(client, key)
        token = auth(make_token(key, job=second))
        assert client.delete(f"{JOBS}/{first}", headers=token).status_code == 404
        assert client.post(f"{JOBS}/{first}/finish", headers=token).status_code == 404

    def test_token_without_a_job_opens_no_existing_job(self, client, key):
        # the token of a start carries no job: it must not be good for anything else
        job_id = start(client, key)
        assert client.delete(f"{JOBS}/{job_id}", headers=auth(make_token(key))).status_code == 404

    def test_foreign_job_is_indistinguishable_from_a_missing_one(self, client, key):
        job_id = start(client, key, "alice")
        foreign = client.delete(f"{JOBS}/{job_id}", headers=auth(make_token(key, "bob", job=job_id)))
        missing_id = "0" * 32
        missing = client.delete(f"{JOBS}/{missing_id}", headers=auth(make_token(key, "bob", job=missing_id)))
        assert foreign.status_code == missing.status_code == 404
        assert foreign.json()["detail"].replace(job_id, "ID") == missing.json()["detail"].replace(missing_id, "ID")

    def test_missing_token_for_an_existing_job_is_401_not_404(self, client, key):
        job_id = start(client, key)
        assert client.delete(f"{JOBS}/{job_id}").status_code == 401

    def test_job_started_before_the_check_was_switched_on_is_closed(self, client, key):
        from app.models import MergeJobStartParams

        job_id = app.state.job_manager.create_job(MergeJobStartParams())
        assert client.delete(f"{JOBS}/{job_id}", headers=auth(make_token(key, job=job_id))).status_code == 404

    def test_api_key_and_token_are_independent_layers(self, client, key, monkeypatch):
        monkeypatch.setenv("API_KEY", "secret")
        job_id = start_with_key(client, key)
        assert client.delete(f"{JOBS}/{job_id}", headers={**auth(make_token(key, job=job_id)), "X-API-Key": "wrong"}).status_code == 401
        assert client.delete(f"{JOBS}/{job_id}", headers={"X-API-Key": "secret"}).status_code == 401
        assert client.delete(f"{JOBS}/{job_id}", headers={**auth(make_token(key, job=job_id)), "X-API-Key": "secret"}).status_code == 204


def start_with_key(client, key) -> str:
    response = client.post(f"{JOBS}/start", json={}, headers={**auth(make_token(key)), "X-API-Key": "secret"})
    assert response.status_code == 201
    return response.json()["jobId"]


class TestKeySet:
    def test_unreachable_key_set_is_503(self, client, key, jwks):
        jwks.unreachable = True
        assert client.post(f"{JOBS}/start", json={}, headers=auth(make_token(key))).status_code == 503

    def test_key_set_is_fetched_once_for_many_calls(self, client, key, jwks):
        for _ in range(5):
            start(client, key)
        assert jwks.fetches == 1

    def test_replaced_key_behind_the_same_kid_is_picked_up(self, client, key, other_key, jwks):
        start(client, key)  # caches the key
        jwks.publish(other_key)  # Polarion restarted and made a new key under the same kid
        assert client.post(f"{JOBS}/start", json={}, headers=auth(make_token(other_key))).status_code == 201
        # and the old key is no longer good
        assert client.post(f"{JOBS}/start", json={}, headers=auth(make_token(key))).status_code == 401

    def test_forged_signatures_cannot_make_polarion_fetch_per_call(self, client, key, other_key, jwks):
        start(client, key)  # caches the key set
        before = jwks.fetches
        for _ in range(50):
            assert client.post(f"{JOBS}/start", json={}, headers=auth(make_token(other_key))).status_code == 401
        assert jwks.fetches - before <= 1

    def test_unknown_kid_costs_nothing_per_call_once_the_set_is_fresh(self, client, key, jwks):
        start(client, key)
        before = jwks.fetches
        for _ in range(20):
            assert client.post(f"{JOBS}/start", json={}, headers=auth(make_token(key, kid="nobody"))).status_code == 401
        assert jwks.fetches - before <= 1  # the library cooldown allows one forced refresh

    def test_a_key_set_which_is_not_json_is_unreachable_not_a_crash(self, client, key, jwks):
        def not_json():
            raise json.JSONDecodeError("Expecting value", "<html>", 0)

        jwks.fetch_data = not_json  # e.g. a proxy answering with an HTML page
        response = client.post(f"{JOBS}/start", json={}, headers=auth(make_token(key)))
        assert response.status_code == 503

    def test_verifier_reports_an_unreachable_key_set(self, key):
        jwks = FakeJwksClient(key)
        jwks.unreachable = True
        with pytest.raises(JwksUnavailableError):
            PolarionTokenVerifier(jwks, 900).verify(make_token(key))


def polarion_shaped_jwks(private_key: rsa.RSAPrivateKey) -> dict:
    """The key set as Polarion publishes it (/polarion/.well-known/jwks.json): a public key which also carries ``"d": null``."""
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    return {"keys": [{"kty": "RSA", "alg": "RS256", "kid": KID, "e": jwk["e"], "d": None, "n": jwk["n"]}]}


@contextlib.contextmanager
def polarion_serves(data):
    """Polarion answers a request for its key set with this JSON, through the real fetch of PyJWKClient."""
    with patch("urllib.request.OpenerDirector.open", side_effect=lambda *args, **kwargs: io.BytesIO(json.dumps(data).encode())):
        yield


POLARION_URL = "http://polarion/polarion/.well-known/jwks.json"


class TestKeySetOfPolarion:
    def test_pyjwt_refuses_the_key_set_as_polarion_publishes_it(self, key):
        # why PolarionJwkClient exists: without it every token of Polarion would be refused
        with polarion_serves(polarion_shaped_jwks(key)), pytest.raises(jwt.PyJWKSetError):
            PyJWKClient(POLARION_URL).get_signing_key(KID)

    def test_the_client_reads_it(self, key):
        with polarion_serves(polarion_shaped_jwks(key)):
            signing_key = PolarionJwkClient(POLARION_URL).get_signing_key(KID)
        assert signing_key.key.public_numbers() == key.public_key().public_numbers()

    def test_a_token_of_polarion_is_verified_against_it(self, key):
        with polarion_serves(polarion_shaped_jwks(key)):
            principal = PolarionTokenVerifier(PolarionJwkClient(POLARION_URL), 900).verify(make_token(key, "alice", job="job-1"))
        assert principal.user_hash == hash_user("alice")
        assert principal.job == "job-1"

    def test_a_forged_token_is_still_refused(self, key, other_key):
        with polarion_serves(polarion_shaped_jwks(key)), pytest.raises(polarion_auth.TokenRejectedError, match="InvalidSignatureError"):
            PolarionTokenVerifier(PolarionJwkClient(POLARION_URL), 900).verify(make_token(other_key))

    def test_a_replaced_key_is_picked_up_through_the_real_fetch(self, key, other_key):
        client = PolarionJwkClient(POLARION_URL, cache_keys=False, lifespan=300)
        verifier = PolarionTokenVerifier(client, 900)
        with polarion_serves(polarion_shaped_jwks(key)):
            verifier.verify(make_token(key))
        with polarion_serves(polarion_shaped_jwks(other_key)):
            verifier.verify(make_token(other_key))

    def test_the_private_members_are_dropped_whatever_they_hold(self, key):
        jwks = polarion_shaped_jwks(key)
        jwks["keys"][0].update({"d": "AQAB", "p": "AQAB", "q": "AQAB", "dp": "AQAB", "dq": "AQAB", "qi": "AQAB"})
        cleaned = PolarionJwkClient._as_jwk_set_payload(jwks)
        assert set(cleaned["keys"][0]) == {"kty", "alg", "kid", "e", "n"}
        # the key set which was passed in is left as it was
        assert "d" in jwks["keys"][0]

    @pytest.mark.parametrize("data", [{}, {"keys": "not a list"}, {"keys": [None, "x"]}])
    def test_a_malformed_key_set_is_passed_on_for_the_library_to_refuse(self, data):
        assert PolarionJwkClient._as_jwk_set_payload(data) == data

    def test_something_which_is_not_a_json_object_is_refused_as_the_library_refuses_it(self):
        with pytest.raises(jwt.PyJWKClientError):
            PolarionJwkClient._as_jwk_set_payload([])

    def test_the_verifier_is_built_on_it(self):
        with patch.object(polarion_auth, "POLARION_JWKS_URL", "https://polarion/polarion/.well-known/jwks.json"):
            verifier = polarion_auth.build_verifier()
        assert isinstance(verifier._jwks_client, PolarionJwkClient)


class TestHostOfTheKeySet:
    """Polarion answers a request for its keys only under the host name of its own base URL."""

    @pytest.mark.parametrize("host", ["localhost", "localhost:80", "polarion.example.com", "10.0.0.5", "10.0.0.5:8080", "[::1]", "[::1]:8080", "a-b.c"])
    def test_a_host_name_or_an_address_is_taken(self, host):
        assert polarion_auth.jwks_headers(host) == {"Host": host}

    @pytest.mark.parametrize("host", ["bad host", "a\r\nX-Injected: yes", "x/y", "a@b", "host:port", "host:123456", "", " ", "http://localhost", "a,b"])
    def test_anything_else_stops_the_service_from_starting(self, host):
        with pytest.raises(ValueError, match="POLARION_JWKS_HOST"):
            polarion_auth.jwks_headers(host)

    def test_no_host_means_no_header(self):
        assert polarion_auth.jwks_headers(None) is None

    def test_the_key_set_is_asked_for_under_that_name(self, key):
        sent = []

        def served(request, *args, **kwargs):
            sent.append(request)
            return io.BytesIO(json.dumps(polarion_shaped_jwks(key)).encode())

        with (
            patch.object(polarion_auth, "POLARION_JWKS_URL", "http://polarion-2606/polarion/.well-known/jwks.json"),
            patch.object(polarion_auth, "POLARION_JWKS_HOST", "localhost"),
            patch("urllib.request.OpenerDirector.open", side_effect=served),
        ):
            verifier = polarion_auth.build_verifier()
            assert verifier is not None
            principal = verifier.verify(make_token(key, "alice", job="job-1"))

        # connected to the address it was given, asked under the name it was told
        assert sent[0].full_url == "http://polarion-2606/polarion/.well-known/jwks.json"
        assert sent[0].get_header("Host") == "localhost"
        assert principal.job == "job-1"

    def test_without_the_setting_the_request_names_no_host(self, key):
        sent = []

        def served(request, *args, **kwargs):
            sent.append(request)
            return io.BytesIO(json.dumps(polarion_shaped_jwks(key)).encode())

        with (
            patch.object(polarion_auth, "POLARION_JWKS_URL", "http://polarion/polarion/.well-known/jwks.json"),
            patch.object(polarion_auth, "POLARION_JWKS_HOST", None),
            patch("urllib.request.OpenerDirector.open", side_effect=served),
        ):
            verifier = polarion_auth.build_verifier()
            assert verifier is not None
            verifier.verify(make_token(key))

        assert sent[0].get_header("Host") is None

    def test_an_invalid_name_stops_the_verifier_from_being_built(self):
        with (
            patch.object(polarion_auth, "POLARION_JWKS_URL", "http://polarion/polarion/.well-known/jwks.json"),
            patch.object(polarion_auth, "POLARION_JWKS_HOST", "bad host"),
            pytest.raises(ValueError, match="POLARION_JWKS_HOST"),
        ):
            polarion_auth.build_verifier()


class TestProbes:
    def test_health_and_version_need_no_token(self, client, jwks):
        assert client.get("/health").status_code in (200, 503)
        assert client.get("/version").status_code == 200
        assert jwks.fetches == 0
