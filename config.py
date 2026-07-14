"""
Application configuration via pydantic-settings.
Loads settings from environment variables and .env file.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Global application settings."""

    # Database
    DATABASE_URL: str = "sqlite:///./quantedge.db"

    # JWT / Auth
    SECRET_KEY: str = "your-secret-key-change-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440

    # Web3 / Blockchain
    WEB3_RPC_URL: str = "https://mainnet.infura.io/v3/YOUR_PROJECT_ID"
    USDT_CONTRACT: str = "0xdAC17F958D2ee523a2206206994597C13D831ec7"
    USDC_CONTRACT: str = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"

    # Payment receiving wallet (EVM chains: ETH/BSC/Polygon/Arbitrum)
    PAYMENT_WALLET_ADDRESS: str = ""  # Your wallet address to receive payments
    # Bitcoin receiving address
    BTC_PAYMENT_ADDRESS: str = ""  # Your BTC address to receive payments
    # Tron receiving address (for USDT TRC-20)
    TRX_PAYMENT_ADDRESS: str = ""  # Your Tron address to receive USDT
    TRON_PRO_API_KEY: str = ""  # Optional TronGrid rate-limit key

    # DeepSeek AI API (for natural language strategy generation)
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_API_URL: str = "https://api.deepseek.com/v1/chat/completions"
    DEEPSEEK_MODEL: str = "deepseek-v4-flash"

    # Google OAuth
    GOOGLE_CLIENT_ID: str = ""

    # Hyperliquid DEX
    HYPERLIQUID_API_URL: str = "https://api.hyperliquid.xyz"
    HYPERLIQUID_TESTNET_URL: str = "https://api.hyperliquid-testnet.xyz"
    HYPERLIQUID_TESTNET: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
    )


# Singleton settings instance
settings = Settings()
