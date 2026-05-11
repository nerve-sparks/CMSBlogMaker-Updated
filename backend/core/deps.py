from fastapi import Header, HTTPException, Depends
import logging
import requests
import jwt

from core.config import settings

logger = logging.getLogger(__name__)

# Dictionary to cache the public keys so we do not spam the Auth server
JWKS_CACHE = {}

def get_public_key(kid: str):
    """
    Fetches the public key from the Auth Gateway.
    Uses requests to prevent 403 Forbidden bot-blocking from WAFs.
    Handles nested 'data' JSON structures.
    """
    if kid in JWKS_CACHE:
        return JWKS_CACHE[kid]
        
    try:
        headers = {"User-Agent": "CMS-Backend/1.0"}
        response = requests.get(settings.AUTH_JWKS_URL, headers=headers, timeout=10)
        response.raise_for_status()
        jwks_resp = response.json()
        
        # Accommodate the nested "data" structure from the custom Auth Gateway
        keys = jwks_resp.get("keys") or jwks_resp.get("data", {}).get("keys", [])
        
        jwk = next((k for k in keys if k.get("kid") == kid), None)
        if not jwk:
            raise Exception(f"Key ID {kid} not found in JWKS endpoint")
            
        # Convert the JWK dictionary into a usable RSA public key
        pub_key = jwt.algorithms.RSAAlgorithm.from_jwk(jwk)
        JWKS_CACHE[kid] = pub_key
        return pub_key
        
    except Exception as e:
        logger.error(f"Failed to fetch or parse JWKS: {str(e)}")
        raise HTTPException(status_code=403, detail="Could not verify token signature keys")


async def get_current_user(authorization: str = Header(default="")):
    """
    Validates the JWT token using the custom JWKS fetcher.
    Returns the user data and tenant context directly from the token payload.
    """
    if not authorization.startswith("Bearer "):
        logger.warning("Missing Bearer token in authorization header")
        raise HTTPException(status_code=401, detail="Missing or invalid token format")

    token = authorization.replace("Bearer ", "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Empty token")
        
    try:
        # Extract the unverified header to find the Key ID (kid)
        unverified_header = jwt.get_unverified_header(token)
        kid = unverified_header.get("kid")
        if not kid:
            raise HTTPException(status_code=401, detail="Token missing kid header")
            
        # Fetch the correct public signing key
        public_key = get_public_key(kid)
        
        # Decode and cryptographically verify the token
        payload = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            issuer=settings.JWT_ISSUER,
            audience=settings.JWT_AUDIENCE
        )
        
        # Extract the required context directly from the verified payload
        user_data = {
            "id": payload.get("sub"),
            "email": payload.get("email"),
            "name": payload.get("display_name") or payload.get("email", "").split("@")[0],
            "role": payload.get("role", "user"),
            "tenant_id": payload.get("tenant_id")
        }
        
        logger.info(f"User authenticated: {user_data['email']} | Role: {user_data['role']} | Tenant: {user_data['tenant_id']}")
        return user_data

    except jwt.ExpiredSignatureError:
        logger.warning("Authentication failed: Token has expired")
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.PyJWTError as e:
        logger.warning(f"Authentication failed: Invalid token signature or format. Details: {str(e)}")
        raise HTTPException(status_code=401, detail="Invalid token")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected authentication error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Authentication server error")


async def require_admin(user: dict = Depends(get_current_user)):
    """
    Middleware dependency to ensure the authenticated user has administrative privileges.
    """
    if user.get("role") != "admin":
        logger.warning(f"Access denied: User {user.get('email')} attempted to access an admin-only route.")
        raise HTTPException(
            status_code=403, 
            detail="Admin privileges required to perform this action."
        )
    return user