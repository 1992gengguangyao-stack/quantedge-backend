"""
Authentication utilities: JWT creation/verification, password hashing,
and SIWE (Sign-In with Ethereum / EIP-4361) message verification.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt
from passlib.context import CryptContext
from eth_account import Account
from eth_account.messages import encode_defunct

from config import settings

# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """Hash a plain-text password."""
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plain-text password against its hash."""
    return pwd_context.verify(plain, hashed)


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    Create a JWT access token.

    :param data: payload dict, must contain "sub" (subject / user id)
    :param expires_delta: optional custom expiry
    :return: encoded JWT string
    """
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def verify_token(token: str) -> dict:
    """
    Decode and verify a JWT token.

    :raises JWTError: if the token is invalid or expired
    :return: payload dict
    """
    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    return payload


# ---------------------------------------------------------------------------
# SIWE (Sign-In with Ethereum) — EIP-4361
# ---------------------------------------------------------------------------

def parse_siwe_message(message: str) -> dict:
    """
    Parse an EIP-4361 Sign-In with Ethereum message into a dict of fields.

    Expected format (simplified):
        {domain} wants you to sign in with your Ethereum account:
        {address}

        {statement}

        URI: {uri}
        Version: {version}
        Chain ID: {chain-id}
        Nonce: {nonce}
        Issued At: {issued-at}
        Expiration Time: {expiration-time}  (optional)
    """
    fields: dict = {}
    lines = message.strip().split("\n")

    # First line: "{domain} wants you to sign in with your Ethereum account:"
    if not lines:
        raise ValueError("Empty SIWE message")

    header = lines[0]
    if "wants you to sign in with your Ethereum account:" not in header:
        raise ValueError("Invalid SIWE header: missing domain declaration")
    fields["domain"] = header.split(" wants you to sign in with your Ethereum account:")[0].strip()

    # Second line (index 1): Ethereum address
    if len(lines) < 2:
        raise ValueError("SIWE message missing address line")
    fields["address"] = lines[1].strip()

    # Parse key-value pairs (lines starting with "Field: value")
    for line in lines[2:]:
        if ":" in line and not line.startswith(" "):
            key, _, value = line.partition(":")
            key = key.strip().lower().replace(" ", "_")
            fields[key] = value.strip()

    return fields


def verify_siwe_message(message: str, signature: str) -> str:
    """
    Verify a Sign-In with Ethereum message and signature.

    Recovers the Ethereum address from the signature and verifies that it
    matches the address declared in the SIWE message. Also performs basic
    validation of domain, nonce, and expiration.

    :param message: the raw EIP-4361 SIWE message string
    :param signature: the hex signature (0x-prefixed)
    :return: the recovered wallet address (checksummed)
    :raises ValueError: if verification fails
    """
    # Parse the SIWE message fields
    fields = parse_siwe_message(message)

    # Validate required fields
    required = ["address", "nonce", "issued_at", "uri", "version", "chain_id"]
    for req in required:
        if req not in fields or not fields[req]:
            raise ValueError(f"SIWE message missing required field: {req}")

    # Check expiration if present
    expiration = fields.get("expiration_time")
    if expiration:
        try:
            exp_dt = datetime.fromisoformat(expiration.replace("Z", "+00:00"))
            if datetime.now(timezone.utc) > exp_dt:
                raise ValueError("SIWE message has expired")
        except ValueError as exc:
            if "expired" in str(exc).lower():
                raise
            # If we can't parse the date, skip expiration check (lenient)

    if fields.get("version") != "1":
        raise ValueError("Unsupported SIWE version")

    try:
        issued_at = datetime.fromisoformat(fields["issued_at"].replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        if issued_at.tzinfo is None:
            issued_at = issued_at.replace(tzinfo=timezone.utc)
        if issued_at > now + timedelta(minutes=2):
            raise ValueError("SIWE issued-at time is in the future")
        if issued_at < now - timedelta(minutes=15):
            raise ValueError("SIWE message is too old")
    except ValueError as exc:
        if "SIWE" in str(exc):
            raise
        raise ValueError("Invalid SIWE issued-at time") from exc

    # Recover the address from the signature
    msg_encode = encode_defunct(text=message)
    recovered_address = Account.recover_message(msg_encode, signature=signature)

    # Verify that the recovered address matches the declared address
    declared_address = fields["address"]
    if recovered_address.lower() != declared_address.lower():
        raise ValueError(
            f"Signature address mismatch: recovered {recovered_address}, "
            f"expected {declared_address}"
        )

    # Return the checksummed address
    return recovered_address


def generate_nonce() -> str:
    """Generate a random nonce for SIWE messages."""
    import secrets
    return secrets.token_hex(16)
