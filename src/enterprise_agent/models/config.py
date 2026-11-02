from typing import Any

from pydantic import BaseModel, Field, field_validator


class ProviderConfig(BaseModel):
    name: str
    protocol: str
    vendor: str | None = None
    endpoint: str | None = None
    credential_ref: str | None = None
    config_version: str = "1"

    @field_validator("credential_ref")
    @classmethod
    def credential_is_reference(cls, value: str | None) -> str | None:
        if value is not None and ("=" in value or value.startswith("sk-") or len(value) > 128):
            raise ValueError("credential_ref must be an environment-variable name, not a secret")
        return value

    @field_validator("vendor")
    @classmethod
    def vendor_is_named(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("vendor must not be blank")
        return value


class ModelProfile(BaseModel):
    name: str
    provider: str
    model_name: str
    timeout_seconds: float = Field(default=30, gt=0)
    temperature: float = Field(default=0, ge=0, le=2)
    parameters: dict[str, Any] = Field(default_factory=dict)
    capabilities: set[str] = Field(default_factory=set)
    structured_output: bool = True
    tool_calling: bool = True


class RoleBinding(BaseModel):
    role: str
    model_profile: str


class ModelConfig(BaseModel):
    providers: list[ProviderConfig]
    profiles: list[ModelProfile]
    bindings: list[RoleBinding]
