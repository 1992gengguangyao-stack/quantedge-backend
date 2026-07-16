"""
FastAPI shared dependencies.
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.orm import Session

from auth import verify_token
from billing import expire_user_plan_if_needed
from database import get_db
from models import User

# OAuth2 scheme — token is read from the Authorization: Bearer <token> header
bearer_scheme = HTTPBearer()

# Alternative: allow raw Authorization header parsing (for convenience)
# The OAuth2PasswordBearer already handles Bearer tokens, so we use it directly.


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """
    Dependency: extract and verify the JWT from the Authorization header,
    then return the corresponding User object.

    :raises HTTPException 401: if token is invalid or user not found
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = verify_token(credentials.credentials)
        user_id: str | None = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = db.query(User).filter(User.id == int(user_id)).first()
    if user is None:
        raise credentials_exception
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive",
        )
    if expire_user_plan_if_needed(user):
        db.commit()
        db.refresh(user)
    return user
