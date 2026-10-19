from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SANDBOX_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./sandbox.sqlite3"
    gateway_token: str
    coolify_fake: bool = True
    coolify_url: str = "https://coolify.example"
    coolify_token: str = ""
    coolify_project_uuid: str = ""
    coolify_server_uuid: str = ""
    coolify_environment_name: str = "production"
    apps_base_domain: str = "apps.example"
    ttl_days: int = 14
    workdir: str = "/tmp/sandbox-mcp"
    host: str = "0.0.0.0"
    port: int = 8080
    lease_seconds: int = 600
    deploy_timeout_seconds: int = 900
    litellm_url: str = "http://litellm:4000"
    identity_cache_seconds: int = 300
