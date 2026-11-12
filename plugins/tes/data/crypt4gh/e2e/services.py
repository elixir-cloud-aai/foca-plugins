"""Identity provider and file server of the end-to-end test.

Serves, for the Compose stack of `compose.yaml`:

- an OpenID Connect identity provider for proTES (issuer `ISSUER`;
  discovery and keys), which issues access tokens of the test user, with
  the group that proTES maps to its role `user` (`config.yaml`), at
  `GET /oidc/token` (no login);
- the files of the directory given as argument at `GET /data/<name>` (the
  task inputs: a Crypt4GH file and a secret key, made by `check.sh`), as
  TES backends download inputs by URL.

Run it with `python services.py DIRECTORY [PORT]` (default port 8080); it
depends on the standard library, PyJWT and cryptography (dependencies of
FOCA, in the image of proTES). For tests only: tokens are issued to anyone
who asks.
"""

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import time
from typing import ClassVar

from cryptography.hazmat.primitives.asymmetric import rsa
import jwt

# Issuer, as proTES reaches it (`config.yaml`)
ISSUER = "http://services:8080/oidc/"
AUDIENCE = "e2e-client"
SUBJECT = "e2e-user"
GROUPS = ["e2e-users"]
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class Handler(BaseHTTPRequestHandler):
    """Handler of the requests to the identity provider and the files."""

    #: Directory of the files served at `/data/`
    directory: ClassVar[Path]

    def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        """Send a response.

        Args:
            status: HTTP status.
            body: Body.
            content_type: Media type of the body.
        """
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, body: object) -> None:
        """Send a JSON response with status 200.

        Args:
            body: JSON body.
        """
        self._send(HTTPStatus.OK, json.dumps(body).encode(), "application/json")

    def _token(self) -> str:
        """Issue an access token of the test user (RFC 9068).

        Returns:
            The signed token.
        """
        now = int(time.time())
        claims = {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": SUBJECT,
            "groups": GROUPS,
            "iat": now,
            "exp": now + 3600,
        }
        return jwt.encode(
            claims, KEY, algorithm="RS256", headers={"kid": "e2e", "typ": "at+jwt"}
        )

    def do_GET(self) -> None:
        """Serve the identity provider and the files."""
        path = self.path.split("?", 1)[0]
        if path == "/oidc/.well-known/openid-configuration":
            self._send_json(
                {
                    "issuer": ISSUER,
                    "jwks_uri": f"{ISSUER}jwk",
                    "token_endpoint": f"{ISSUER}token",
                }
            )
        elif path == "/oidc/jwk":
            key = jwt.algorithms.RSAAlgorithm.to_jwk(KEY.public_key(), as_dict=True)
            self._send_json({"keys": [{**key, "kid": "e2e", "use": "sig"}]})
        elif path == "/oidc/token":
            self._send_json({"access_token": self._token()})
        elif path == "/health":
            self._send_json({"status": "ok"})
        elif path.startswith("/data/"):
            name = path.removeprefix("/data/")
            file = self.directory / name
            # Plain names of files in the directory only
            if "/" in name or name.startswith(".") or not file.is_file():
                self._send(HTTPStatus.NOT_FOUND, b"not found\n", "text/plain")
                return
            self._send(HTTPStatus.OK, file.read_bytes(), "application/octet-stream")
        else:
            self._send(HTTPStatus.NOT_FOUND, b"not found\n", "text/plain")


def main() -> None:
    """Serve until stopped."""
    Handler.directory = Path(sys.argv[1])
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8080
    # All interfaces of the container, for the Compose network
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)  # noqa: S104
    server.serve_forever()


if __name__ == "__main__":
    main()
